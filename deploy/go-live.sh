#!/usr/bin/env bash
# From nothing to a live server, in one command: make the machine (waiting out the region's capacity), wait
# for it to answer SSH, prepare it, and deploy — then print the address and the setup token.
#
#   deploy/go-live.sh                      served under <ip>.sslip.io, which needs no domain of your own
#   NEUROCODE_DOMAIN=eurex.dev deploy/go-live.sh    when that name already points at the machine
#
# Safe to run again: each step finds what it already made. Leave it running — a free Ampere machine can take
# a while to come free, and this takes it the moment it does.
# shellcheck source=deploy/_common.sh
. "$(dirname "$0")/_common.sh"

bold "1/4 · The machine"
"$DEPLOY_DIR/oci-create.sh"
IP=$(SUPPRESS_LABEL_WARNING=True command oci compute instance list \
       --compartment-id "$(awk -F= '/^[ \t]*tenancy[ \t]*=/{gsub(/[ \t]/,"",$2); print $2; exit}' ~/.oci/config)" \
       --display-name "${NEUROCODE_VM_NAME:-neurocode}" --lifecycle-state RUNNING --query 'data[0].id' --raw-output \
     | xargs -I{} env SUPPRESS_LABEL_WARNING=True oci compute instance list-vnics --instance-id {} \
       --query 'data[0]."public-ip"' --raw-output)
[ -n "$IP" ] && [ "$IP" != null ] || die "The machine was made but has no public address yet — run this again."
resolve_host "ubuntu@$IP" >/dev/null

bold "2/4 · Waiting for it to answer"
# A new machine boots, brings up its network and starts sshd; until then a connection is refused, not failed.
for i in $(seq 1 60); do
  ssh_to true 2>/dev/null && break
  [ "$i" = 60 ] && die "$IP never answered SSH. Check the instance in the console."
  sleep 10
done
note "$HOST answers"

bold "3/4 · Preparing it"
ssh_to 'bash -s' < "$DEPLOY_DIR/setup-vm.sh"

bold "4/4 · The first release"
# sslip.io answers any <ip>.sslip.io with that address, so Let's Encrypt can issue for it and the very first
# deploy is real HTTPS. deploy/domain.sh moves it to a name of your own whenever DNS is ready.
NEUROCODE_DOMAIN=${NEUROCODE_DOMAIN:-$IP.sslip.io} "$DEPLOY_DIR/push.sh" "$HOST"
