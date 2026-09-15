#!/usr/bin/env bash
set -euo pipefail

# Renews the wildcard Let's Encrypt cert for athenaindex.app / *.athenaindex.app
# and reloads nginx so it actually picks up a renewed cert.
#
# A wildcard cert can only be issued/renewed via a DNS-01 challenge — NOT the
# HTTP-01 webroot challenge that nginx/default.conf's /.well-known/acme-challenge/
# block is set up for, which is why that block alone was never enough to keep
# this cert alive. This uses certbot's route53 plugin instead, authenticating
# via this EC2 instance's IAM role (no static AWS keys), same as S3 access
# already does — see the comment in docker-compose.prod.yml. The role needs
# route53:ListHostedZones, route53:GetChange, and
# route53:ChangeResourceRecordSets on the athenaindex.app hosted zone; see
# systemd/README.md for one-time host setup.
#
# `certonly --keep-until-expiring` (rather than `certbot renew`) is
# deliberate: it re-asserts the route53 authenticator every run regardless of
# whatever authenticator is recorded in /etc/letsencrypt/renewal/*.conf from
# however this cert was originally issued, and is a no-op unless the cert is
# within 30 days of expiring.

cd /opt/athenaeum

# .env.production is a Compose env file, not something the host shell loads
# on its own — pull LETSENCRYPT_EMAIL out of it the same way deploy.sh's
# `--env-file .env.production` does for the containers.
set -a
# shellcheck disable=SC1091
source .env.production
set +a

certbot certonly \
  --non-interactive \
  --agree-tos \
  --email "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL in .env.production}" \
  --dns-route53 \
  --keep-until-expiring \
  -d athenaindex.app \
  -d '*.athenaindex.app' \
  --deploy-hook "docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload"
