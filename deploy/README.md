# NeuroCode on an Oracle Cloud Always Free server

One small VM runs everything: Postgres with pgvector, the API, and Caddy serving the web app over HTTPS on your
domain. Only ports 80 and 443 face the internet. Oracle's Always Free Ampere A1 shape (up to 4 cores and 24 GB of
memory) is plenty.

## 1. The server (Oracle Cloud console)

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
# once: Docker, the host firewall, /opt/neurocode
ssh -i ~/.ssh/neurocode_oci ubuntu@<ip> 'bash -s' < deploy/setup-vm.sh

# every release: build, copy, (re)start. The first run writes the server's .env with random secrets and prints
# the setup token.
deploy/push.sh ubuntu@<ip>
```

Then open `https://eurex.dev`. The setup wizard asks for the **setup token** once, before it makes the first
Owner — so nobody who finds the address first can claim the workspace. Add a model key in Admin → AI providers.

## What runs where

| Piece | Where | Kept in |
|---|---|---|
| Postgres 16 + pgvector | `db` container, private network only | volume `pgdata` |
| API | `api` container, private network only | volume `data` — cloned repos, worktrees, backups, keys (`secrets.json`) |
| Web app + HTTPS | `web` (Caddy), ports 80/443 | volume `caddy_data` — certificates |
| Secrets | `/opt/neurocode/deploy/.env` on the server, mode 600 | never in git |

- **Machine access** (browsing the server's folders, a terminal, run and debug) is **off** here
  (`NEUROCODE_MACHINE_ACCESS=false`): turning it on gives whoever signs in as an Owner a shell on the server.
- **Backups:** Admin → Database → Back up now writes a `pg_dump` into the `data` volume. Copy one off the server
  now and then: `ssh -i ~/.ssh/neurocode_oci ubuntu@<ip> 'docker compose -f /opt/neurocode/deploy/docker-compose.yml exec -T api ls /data/backups'`.
- **Logs:** `docker compose logs -f api` (or `web`, `db`) in `/opt/neurocode/deploy`.

## Closing the old Oracle account

In the old account, first delete what it runs: **Compute → Instances** (terminate each, and tick "permanently
delete the boot volume"), then **Block Storage → Boot/Block Volumes**, **Networking → Reserved public IPs**, and
any Object Storage buckets. Then close the account itself from the console's account/billing pages; if the
console offers no close option for a Free Tier account, open a support request asking for the account to be
terminated. Keep the old domain's DNS pointed at the new server's IP only.
