#!/usr/bin/env bash
# Make the server this stack runs on, in an Oracle Cloud Always Free tenancy, with the OCI CLI: a virtual
# network with one public subnet, the two ports the web needs, and an Ampere VM running Ubuntu 24.04 with
# your SSH key on it. It prints the public IP, which `setup-vm.sh` and `push.sh` then take.
#
#   deploy/oci-create.sh                 (profile DEFAULT in ~/.oci/config)
#   OCI_PROFILE=neuro deploy/oci-create.sh
#
# It is safe to run again: everything it makes is found by name and reused, so a run that stopped halfway
# carries on from where it stopped. Always Free covers 4 Ampere cores and 24 GB of memory in total, so this
# asks for exactly that — and if the region has no Ampere capacity at that moment (which is common, and
# says nothing about your account), it keeps asking rather than giving up.
set -euo pipefail

PROFILE=${OCI_PROFILE:-DEFAULT}
NAME=${NEUROCODE_VM_NAME:-neurocode}
# What to ask for, in order, as `cores:gigabytes`. Always Free covers 4 Ampere cores and 24 GB in total, so the
# first entry is the whole allowance; the smaller ones are there because a region that has no room for four
# cores often has room for one, and an A1 machine can be given the rest later (Compute → the instance → Edit →
# shape) without rebuilding it. Anything you get now beats waiting a week for the perfect size.
PLAN=${NEUROCODE_VM_PLAN:-"4:24 2:12 1:6"}
SSH_KEY=${NEUROCODE_SSH_KEY:-$HOME/.ssh/neurocode_oci}
TRIES=${NEUROCODE_CAPACITY_TRIES:-60}        # a capacity error is retried this many times, a minute apart
oci() { command oci --profile "$PROFILE" "$@"; }
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[ -f "$SSH_KEY.pub" ] || { echo "No SSH public key at $SSH_KEY.pub — make one with: ssh-keygen -t ed25519 -f $SSH_KEY -C neurocode-oci-deploy" >&2; exit 1; }
PUBKEY=$(cat "$SSH_KEY.pub")

# The root compartment is the tenancy itself: what a Free Tier account has, unless you made others.
C=${NEUROCODE_COMPARTMENT:-$(awk -F= -v p="[$PROFILE]" '$0==p{f=1;next} /^\[/{f=0} f&&/^[ \t]*tenancy[ \t]*=/{gsub(/[ \t]/,"",$2); print $2; exit}' ~/.oci/config)}
[ -n "$C" ] || { echo "No tenancy OCID found in ~/.oci/config" >&2; exit 1; }
say "Tenancy $C"

find_id() { # find_id <oci list command...> — the id of the first result, or empty
  local out; out=$("$@" --query 'data[0].id' --raw-output 2>/dev/null || true)
  [ "$out" = "null" ] && out=""
  printf '%s' "$out"
}

# ── the network ──────────────────────────────────────────────────
VCN=$(find_id oci network vcn list --compartment-id "$C" --display-name "$NAME-vcn" --lifecycle-state AVAILABLE)
if [ -z "$VCN" ]; then
  say "Creating the virtual network"
  VCN=$(oci network vcn create --compartment-id "$C" --display-name "$NAME-vcn" --cidr-blocks '["10.0.0.0/16"]' \
        --dns-label "$NAME" --wait-for-state AVAILABLE --query 'data.id' --raw-output)
fi
echo "VCN $VCN"

IGW=$(find_id oci network internet-gateway list --compartment-id "$C" --vcn-id "$VCN" --display-name "$NAME-igw")
if [ -z "$IGW" ]; then
  say "Creating the internet gateway"
  IGW=$(oci network internet-gateway create --compartment-id "$C" --vcn-id "$VCN" --display-name "$NAME-igw" \
        --is-enabled true --wait-for-state AVAILABLE --query 'data.id' --raw-output)
fi

RT=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-route-table-id"' --raw-output)
say "Routing everything else to the internet"
oci network route-table update --rt-id "$RT" --force --wait-for-state AVAILABLE >/dev/null \
  --route-rules "[{\"destination\":\"0.0.0.0/0\",\"destinationType\":\"CIDR_BLOCK\",\"networkEntityId\":\"$IGW\"}]"

# SSH so this machine can reach it; 80 and 443 for Caddy (80 is how the certificate is issued and renewed).
# ICMP type 3 code 4 is how a router tells us a packet was too big: without it, some networks hang.
SL=$(oci network vcn get --vcn-id "$VCN" --query 'data."default-security-list-id"' --raw-output)
say "Opening 22, 80 and 443"
oci network security-list update --security-list-id "$SL" --force --wait-for-state AVAILABLE >/dev/null \
  --ingress-security-rules '[
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":80,"max":80}}},
    {"protocol":"6","source":"0.0.0.0/0","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":443,"max":443}}},
    {"protocol":"17","source":"0.0.0.0/0","isStateless":false,"udpOptions":{"destinationPortRange":{"min":443,"max":443}}},
    {"protocol":"1","source":"0.0.0.0/0","isStateless":false,"icmpOptions":{"type":3,"code":4}}
  ]'

SUBNET=$(find_id oci network subnet list --compartment-id "$C" --vcn-id "$VCN" --display-name "$NAME-public" --lifecycle-state AVAILABLE)
if [ -z "$SUBNET" ]; then
  say "Creating the public subnet"
  SUBNET=$(oci network subnet create --compartment-id "$C" --vcn-id "$VCN" --display-name "$NAME-public" \
           --cidr-block 10.0.0.0/24 --dns-label public --prohibit-public-ip-on-vnic false \
           --wait-for-state AVAILABLE --query 'data.id' --raw-output)
fi
echo "Subnet $SUBNET"

# ── the machine ──────────────────────────────────────────────────
ID=$(oci compute instance list --compartment-id "$C" --display-name "$NAME" --lifecycle-state RUNNING \
     --query 'data[0].id' --raw-output 2>/dev/null || true)
if [ -z "$ID" ] || [ "$ID" = "null" ]; then
  IMAGE=$(oci compute image list --compartment-id "$C" --operating-system 'Canonical Ubuntu' \
          --operating-system-version '24.04' --shape VM.Standard.A1.Flex --sort-by TIMECREATED --sort-order DESC \
          --query 'data[0].id' --raw-output)
  [ -n "$IMAGE" ] || { echo "No Ubuntu 24.04 image for Ampere in this region" >&2; exit 1; }
  # macOS ships bash 3.2, which has no `mapfile`, and the CLI answers with JSON: read it with python.
  ADS=()
  while IFS= read -r ad; do [ -n "$ad" ] && ADS+=("$ad"); done < <(
    oci iam availability-domain list --compartment-id "$C" \
    | python3 -c 'import json,sys; [print(a["name"]) for a in json.load(sys.stdin)["data"]]')
  [ ${#ADS[@]} -gt 0 ] || { echo "Could not read this region's availability domains" >&2; exit 1; }
  say "Asking for Ampere capacity (Ubuntu 24.04) — $PLAN — across ${#ADS[@]} availability domain(s)"
  for ((try = 1; try <= TRIES; try++)); do
    for AD in "${ADS[@]}"; do
      for size in $PLAN; do
        cores=${size%%:*}; gb=${size##*:}
        if out=$(oci compute instance launch --availability-domain "$AD" --compartment-id "$C" --display-name "$NAME" \
                 --shape VM.Standard.A1.Flex --shape-config "{\"ocpus\":$cores,\"memoryInGBs\":$gb}" \
                 --image-id "$IMAGE" --subnet-id "$SUBNET" --assign-public-ip true \
                 --metadata "{\"ssh_authorized_keys\":\"$PUBKEY\"}" \
                 --wait-for-state RUNNING --query 'data.id' --raw-output 2>&1); then
          ID=$out; GOT="$cores cores and $gb GB"; break 3
        fi
        grep -qi 'capacity' <<<"$out" || { echo; echo "$out" >&2; exit 1; }
        printf '\r  %s: no room for %s cores / %s GB — try %d of %d…                 ' "$AD" "$cores" "$gb" "$try" "$TRIES"
      done
    done
    sleep 60
  done
  [ -n "${ID:-}" ] || { echo; echo "Still no Ampere capacity after $TRIES tries. Leave it running longer (NEUROCODE_CAPACITY_TRIES=600), or try again at a quieter hour." >&2; exit 1; }
  echo; say "Got ${GOT:-a machine}"
fi
say "Instance $ID"

IP=$(oci compute instance list-vnics --instance-id "$ID" --query 'data[0]."public-ip"' --raw-output)
cat <<DONE

The server is up: $IP

  1. Point your domain at it:      an A record → $IP
  2. Install Docker on it:         ssh -i $SSH_KEY ubuntu@$IP 'bash -s' < deploy/setup-vm.sh
  3. Deploy, and every release:    deploy/push.sh ubuntu@$IP
DONE
