#!/usr/bin/env bash
# Once, on a fresh Ubuntu 22.04 or 24.04 server (an Oracle Cloud Always Free Ampere A1 VM works well):
# Docker and Compose, the host firewall opened for HTTP and HTTPS, and the folder the stack lives in.
#
#   ssh -i ~/.ssh/neurocode_oci ubuntu@<ip> 'bash -s' < deploy/setup-vm.sh
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 rsync iptables-persistent
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# Oracle's Ubuntu images ship iptables rules that reject everything but SSH, on top of the cloud's own security
# list. Both must allow 80 and 443: this is the host half (the security list is a console step, see README).
for rule in "tcp 80" "tcp 443" "udp 443"; do
  set -- $rule
  sudo iptables -C INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 5 -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

sudo mkdir -p /opt/neurocode
sudo chown "$USER":"$USER" /opt/neurocode
echo "ready: Docker $(docker --version | cut -d' ' -f3 | tr -d ,), firewall open on 80 and 443, /opt/neurocode"
