#!/usr/bin/env bash
# Once, on a fresh Ubuntu 22.04 or 24.04 server (an Oracle Cloud Always Free Ampere A1 VM works well): Docker
# and Compose, the host firewall opened for HTTP and HTTPS, log rotation, security updates, a nightly backup,
# and the folder the stack lives in. Running it again changes nothing it has already done.
#
#   ssh -i ~/.ssh/neurocode_oci ubuntu@<ip> 'bash -s' < deploy/setup-vm.sh
set -euo pipefail

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker.io docker-compose-v2 rsync iptables-persistent unattended-upgrades
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# A container's log is a file that grows for ever unless something stops it, and this machine's disk is the
# only disk. Ten megabytes, three files, per container.
sudo install -d /etc/docker
echo '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' \
  | sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker

# Ubuntu's own security updates, applied by itself. A server nobody logs into for a month is the one that
# needs this most.
sudo systemctl enable --now unattended-upgrades

# Oracle's Ubuntu images ship iptables rules that reject everything but SSH, on top of the cloud's own security
# list. Both must allow 80 and 443: this is the host half (the cloud half is in the security list —
# deploy/oci-create.sh opens it, or add the ingress rules in the console).
for rule in "tcp 80" "tcp 443" "udp 443"; do
  # shellcheck disable=SC2086  # "tcp 80" is two words on purpose
  set -- $rule
  sudo iptables -C INPUT -p "$1" --dport "$2" -m state --state NEW -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 5 -p "$1" --dport "$2" -m state --state NEW -j ACCEPT
done
sudo netfilter-persistent save

# A machine with a gigabyte of memory can run this stack, but only with somewhere to put what it is not
# using. Two gigabytes of swap, and a kernel told to reach for it late rather than early.
MEM_MB=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 2500 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-neurocode.conf >/dev/null
  sudo sysctl -p /etc/sysctl.d/99-neurocode.conf >/dev/null
  echo "a small machine (${MEM_MB} MB): 2 GB of swap added"
fi

sudo mkdir -p /opt/neurocode
sudo chown "$USER":"$USER" /opt/neurocode

# A nightly dump, kept for two weeks, inside the volume the API already owns — so a restore needs nothing but
# this machine, and `deploy/backup.sh` can bring one home. It runs as a timer rather than cron because a
# server that was asleep at 03:00 should still take its backup when it wakes (`Persistent=true`).
sudo tee /usr/local/bin/neurocode-backup >/dev/null <<'SCRIPT'
#!/bin/sh
set -eu
cd /opt/neurocode/deploy
name="neurocode-$(date -u +%Y%m%d-%H%M%S).sql.gz"
docker compose exec -T api sh -c "mkdir -p /data/backups && pg_dump --dbname \"\$NEUROCODE_DATABASE_URL\" \
  --format=plain --no-owner --no-privileges | gzip -9 > /data/backups/$name"
docker compose exec -T api sh -c 'find /data/backups -name "neurocode-*.sql.gz" -mtime +14 -delete'
echo "wrote /data/backups/$name"
SCRIPT
sudo chmod 0755 /usr/local/bin/neurocode-backup
sudo tee /etc/systemd/system/neurocode-backup.service >/dev/null <<'UNIT'
[Unit]
Description=NeuroCode database backup
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/neurocode-backup
UNIT
sudo tee /etc/systemd/system/neurocode-backup.timer >/dev/null <<'UNIT'
[Unit]
Description=NeuroCode database backup, nightly
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now neurocode-backup.timer

echo "ready: Docker $(docker --version | cut -d' ' -f3 | tr -d ,), firewall open on 80 and 443, logs rotated,"
echo "       security updates on, a nightly backup at 03:00 UTC, and /opt/neurocode waiting for a release."
