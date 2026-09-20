# Running the panel

What the deployment is made of, how to change it, and what to do when something is wrong. Every
command here is one the deployment scenarios in `backend/tests/test_containers.py`,
`test_edge.py`, and `test_guest.py` run, so they are checked rather than remembered.

## What runs

Four containers on one host, started by systemd from Quadlets in `containers/quadlet/`:

| Service | What it does | Listens on |
| --- | --- | --- |
| `panel-worker` | Collects calls from Metro Nashville. The only writer of the database. | nothing |
| `panel-api` | Read-only GraphQL, the event stream, and the health checks. | `127.0.0.1:8001` |
| `panel-dashboard` | The built dashboard, served as static files. | `127.0.0.1:8080` |
| `panel-caddy` | The public edge: TLS, the OWASP Core Rule Set, and the routes. | the host's `:80` and `:443` |

Only the proxy is reachable from outside: everything else is on the loopback address, which is
also why the addresses in the proxy's logs are the clients' own. `panel-backup.timer` takes a
backup every night.

Two volumes hold everything that has to survive a restart: `panel-data` (the database and its
backups) and `panel-certificates` (what Caddy obtained). `/var/log/caddy` is a directory on the
host, because fail2ban reads the firewall's audit log from there.

## Deploying a change

1. Build the images on the host, from a checkout of this repository:

   ```
   podman build -f containers/backend.Containerfile -t localhost/panel-backend:latest .
   podman build -f containers/dashboard.Containerfile -t localhost/panel-dashboard:latest .
   podman build -f containers/caddy.Containerfile -t localhost/panel-caddy:latest .
   ```

   A deployment whose map tiles are not OpenStreetMap's passes its own:
   `--build-arg VITE_TILE_URL=... --build-arg VITE_TILE_ATTRIBUTION=...`.

2. Configure the host, from this repository on the machine you administer from:

   ```
   cd infra/ansible
   ansible-galaxy collection install --requirements-file requirements.yml
   ansible-playbook -i inventory/your-deployment.ini site.yml
   ```

   The inventory carries what differs between deployments: the site name, the addresses
   administration is accepted from, and, if the certificate comes from a DNS challenge, the
   path to the credentials on **your** machine. Nothing of that belongs in this repository.

3. Restart what changed: `systemctl restart panel-api panel-dashboard panel-caddy`. The worker
   finishes its current request and releases the writer lock when it is asked to stop, so
   `systemctl restart panel-worker` is safe at any moment.

The first start of a new host runs the migrations by itself: the worker owns the schema, and
`/readyz` answers 503 until the schema the API expects is there.

## Backups, and restoring one

`panel-backup.timer` runs `panel backup` at 04:30 with a random delay, which takes a snapshot
through SQLite's online backup — the worker keeps writing throughout — and keeps the newest
seven. They land in `panel-data` under `backups/`, named for the moment they were taken.

To list them:

```
podman run --rm --volume panel-data:/var/lib/panel:z --entrypoint sh \
  localhost/panel-backend:latest -c 'ls /var/lib/panel/backups'
```

To restore one, stop the writer, replace the database, and start again. A backup is a whole
database, so restoring is a copy; the write-ahead log beside the old database has to go with it,
or SQLite will try to replay it onto a database it does not belong to.

```
systemctl stop panel-worker panel-api
podman run --rm --volume panel-data:/var/lib/panel:z --entrypoint sh \
  localhost/panel-backend:latest -c '
    rm -f /var/lib/panel/panel.sqlite /var/lib/panel/panel.sqlite-wal /var/lib/panel/panel.sqlite-shm
    cp /var/lib/panel/backups/panel-20260919T043000123Z.sqlite /var/lib/panel/panel.sqlite'
systemctl start panel-worker panel-api
```

The worker picks up from the checkpoints in the restored database: it re-reads the window it
reconciles and carries on, so a restored panel catches up rather than starting over.

## Certificates

With nothing configured, Caddy issues a certificate itself, which is what a deployment behind
something else, or one being tried out, wants. A deployment that owns a public name and uses
Google Cloud DNS sets three things in its inventory:

```
panel_acme_email=operations@example.org
panel_acme_gcp_project=your-gcp-project
panel_acme_credentials=/home/you/secrets/panel-dns.json
```

Ansible writes the credentials to `/etc/caddy/acme/dns-credentials.json`, readable only by the
proxy, and a `tls` block to `/etc/caddy/conf.d/tls.caddy`. Removing the settings removes both
and gives certificates back to Caddy.

## Watching it

- `curl -fsS https://<site>/readyz` — the API and the schema revision it is serving.
- The dashboard's own status bar reports ingestion: whether the last poll succeeded, when the
  newest call was published, when the source itself was last edited, and backfill progress.
  A successful poll is not the same as new data, which is why they are separate.
- `journalctl -u panel-worker` for ingestion, `-u panel-caddy` for refused requests.
- `fail2ban-client status panel-waf` for who is currently banned.

## When an address is banned by mistake

Five requests the web firewall refused, from one address, within ten minutes, earn an hour off
the whole host. To let someone back in now:

```
fail2ban-client set panel-waf unbanip 203.0.113.10
```

If the refusals are the application's own doing rather than an attack, the request will be in
`/var/log/caddy/coraza-audit.log` with the rule that stopped it. Exclusions belong in
`containers/caddy/coraza/panel.conf`, one rule and one reason at a time, and the scenarios in
`backend/tests/test_edge.py` will tell you whether the dashboard still works with them.

## What is checked, and what is not

`pytest -m deployment` builds the images, runs them under the same restrictions the Quadlets
impose, puts the proxy in front of them, and configures an AlmaLinux 10 guest with the real
playbook, twice, to show the second run changes nothing.

A container is not a machine. Reboot recovery, certificates actually issued by a certificate
authority, SELinux labelling, and packets actually filtered are not covered by any of it, and
want a run against a virtual machine before a deployment is trusted with them.
