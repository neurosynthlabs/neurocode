# NeuroCode on an Oracle Cloud Always Free server

One small VM runs everything: Postgres with pgvector, the API, and Caddy serving the web app over HTTPS on your
domain. Only ports 80 and 443 face the internet. Oracle's Always Free Ampere A1 shape (up to 4 cores and 24 GB of
memory) is plenty.

## 1. The server

Either by hand in the console (below), or in one command with the OCI CLI — `brew install oci-cli`, an API
key added under **Profile → My profile → API keys**, and then:

```bash
deploy/oci-create.sh          # the network, ports 22/80/443, and an Ampere VM (4 cores, 24 GB) on Ubuntu 24.04
```

It reuses whatever it already made, so it is safe to run again, and when the region has no Ampere capacity
(common, and nothing to do with your account) it keeps asking every minute instead of giving up.

### By hand (Oracle Cloud console)

1. Sign in to the Oracle Cloud account you mean to keep (the new one). Pick the home region nearest your users —
   it cannot be changed later.
2. **Compute → Instances → Create instance**
   - Image: **Canonical Ubuntu 24.04** (the aarch64 one appears for Ampere).
   - Shape: **Ampere → VM.Standard.A1.Flex**, 4 OCPUs, 24 GB memory (all inside Always Free). If the region says
     "out of capacity", try another availability domain, or 2 OCPUs / 12 GB.
   - Networking: a public subnet, **Assign a public IPv4 address**.
   - SSH keys: **Paste public keys** — the contents of `~/.ssh/neurocode_oci.pub` on your Mac.
3. **Open the web ports in the cloud firewall:** the instance's subnet → its **Security List** → **Add Ingress
   Rules**: source `0.0.0.0/0`, TCP, destination port `80`; the same for `443`; and UDP `443` (HTTP/3, optional).
4. Note the instance's **public IP**.

## 2. The domain

At the registrar (or DNS host) of `eurex.dev`, add an **A record** for the name you will use — `eurex.dev` itself
(`@`) or a subdomain such as `app.eurex.dev` — pointing at the public IP. `.dev` is HTTPS-only in every browser,
which is fine: Caddy gets the certificate on the first start, as soon as the name resolves to the server.
If you use a subdomain, put it in `DOMAIN` in the server's `.env` after the first push (then push again).

## 3. From your Mac

```bash
# once: Docker, the firewall, log rotation, security updates, a nightly backup, /opt/neurocode
ssh -i ~/.ssh/neurocode_oci ubuntu@<ip> 'bash -s' < deploy/setup-vm.sh

# the first release — the name it is served under, and the server it goes to, are given once
NEUROCODE_DOMAIN=eurex.dev deploy/push.sh ubuntu@<ip>

# every release after that
npm run deploy
```

`push.sh` builds the web app here, copies the tree, builds the API image on the server, starts it, waits for
the API's own health check, and then asks `https://<domain>/api/health` from your machine. A release that
never becomes healthy is put back: the image that was running is retagged and started again, and the deploy
fails with the log that explains why. It takes a lock on the server, so two releases cannot interleave.

With no domain of your own yet, use the machine's own name — `NEUROCODE_DOMAIN=<ip>.sslip.io` — and move it
later with `deploy/domain.sh eurex.dev`. That is real HTTPS either way; Caddy gets the certificate itself.

The rest of the day-to-day:

| Command | What it does |
|---|---|
| `npm run deploy` | a release (the server is remembered in `deploy/.host`) |
| `npm run deploy:status` | containers and health, what the API says, the certificate, disk and memory, last backups |
| `npm run deploy:logs` (`-- web`) | follow a service's log |
| `npm run deploy:backup` | take a dump now and bring it to `deploy/backups/` on this Mac |
| `deploy/domain.sh <name>` | serve it under another name, once that name points here |

**On every push to main:** `.github/workflows/deploy.yml` releases the commit that `verify` just passed, using
the same `push.sh`. It needs two repository secrets — `DEPLOY_HOST` (`ubuntu@<ip>`) and `DEPLOY_SSH_KEY` (the
private half of the deploy key) — and does nothing until they are set.

## What runs where

| Piece | Where | Kept in |
|---|---|---|
| Postgres 16 + pgvector | `db` container, private network only | volume `pgdata` |
| API | `api` container, private network only | volume `data` — cloned repos, worktrees, backups, keys (`secrets.json`) |
| Web app + HTTPS | `web` (Caddy), ports 80/443 | volume `caddy_data` — certificates |
| Secrets | `/opt/neurocode/deploy/.env` on the server, mode 600 | never in git |

- **Machine access** (browsing the server's folders, a terminal, run and debug) is **off** here
  (`NEUROCODE_MACHINE_ACCESS=false`): turning it on gives whoever signs in as an Owner a shell on the server.
  The web app is told, on the session, that this server opens nothing — so the Workbench, the folder pickers
  and Blueprint scaffolding say why instead of offering a folder that cannot be opened. Everything else —
  plans, runs on cloned repositories, sessions, memory, reviews, routines — works the same as locally.
- **Backups:** the server takes one every night at 03:00 UTC (a systemd timer from `setup-vm.sh`) and keeps two
  weeks of them in the `data` volume; Admin → Database → Back up now writes one on demand. `npm run deploy:backup`
  takes a fresh dump and copies it to this Mac, because a backup that only exists on the server it came from is
  not a backup. The restore command is printed with it.
- **Logs:** `docker compose logs -f api` (or `web`, `db`) in `/opt/neurocode/deploy`.

## Closing the old Oracle account

In the old account, first delete what it runs: **Compute → Instances** (terminate each, and tick "permanently
delete the boot volume"), then **Block Storage → Boot/Block Volumes**, **Networking → Reserved public IPs**, and
any Object Storage buckets. Then close the account itself from the console's account/billing pages; if the
console offers no close option for a Free Tier account, open a support request asking for the account to be
terminated. Keep the old domain's DNS pointed at the new server's IP only.
