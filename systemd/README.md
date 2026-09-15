# Cert renewal timer — one-time host setup

`git pull` + `deploy.sh` update everything that runs *inside* Docker, but a
systemd timer lives on the host itself and isn't installed by a normal
deploy. Do this once on the EC2 box (re-run only if the unit files below
change).

## Why this exists

The prod TLS cert (`/etc/letsencrypt/live/athenaindex.app/`) is a wildcard
cert (`athenaindex.app` + `*.athenaindex.app`, covering `www`, `api`, and the
`pr-N` preview subdomains). Wildcard certs can only be issued/renewed via a
**DNS-01** challenge — the `/.well-known/acme-challenge/` webroot block in
`nginx/default.conf` is HTTP-01 and can't renew this cert on its own. Nothing
was renewing it automatically, which is why it expired on 2026-09-05.

## Prerequisites

1. **Install certbot + the Route53 DNS plugin** on the host (not in a
   container — nginx only mounts `/etc/letsencrypt` read-only, it can't renew
   anything itself):
   ```
   sudo apt-get install certbot python3-certbot-dns-route53
   ```
2. **IAM permissions**: the EC2 instance role needs, scoped to the
   `athenaindex.app` hosted zone:
   - `route53:ListHostedZones`
   - `route53:GetChange`
   - `route53:ChangeResourceRecordSets`

   (This is the same "no static keys, use the instance role" pattern already
   used for S3 access — see the comment in `docker-compose.prod.yml`.)
3. **`LETSENCRYPT_EMAIL`** set in `/opt/athenaeum/.env.production` (used for
   Let's Encrypt expiry notices — add it if it isn't there yet).

## Install the timer

```
sudo cp systemd/athenaeum-certbot-renew.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now athenaeum-certbot-renew.timer
```

## Verify

```
# Confirm the timer is scheduled:
systemctl list-timers athenaeum-certbot-renew.timer

# Dry-run the actual renewal logic without hitting Let's Encrypt's real
# rate limits:
certbot certonly --dry-run --dns-route53 --keep-until-expiring \
  -d athenaindex.app -d '*.athenaindex.app'

# Run it for real once, immediately, to reissue the currently-expired cert:
sudo /opt/athenaeum/renew-certs.sh
```
