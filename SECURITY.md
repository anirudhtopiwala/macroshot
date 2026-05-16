# Security Policy

## Reporting a vulnerability

**Do not file a public GitHub issue for a security vulnerability.**

### Preferred: GitHub Private Vulnerability Reporting

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue, ideally with reproduction steps and the affected
   version (commit SHA or tag).

### Email fallback

If GitHub PVR is unavailable, email **macroshotapp@gmail.com** with
subject prefixed `[SECURITY]`. If you require encrypted transport for
a highly sensitive report, mention this in your first message and the
maintainer will share a GPG key on request.

### Response time

- **Triage acknowledgement:** within 3 business days.
- **Initial assessment + severity:** within 7 calendar days.
- **Fix landed in a release:** within 30 days for Critical/High,
  90 days for Medium, best-effort for Low.

If you do not receive an acknowledgement within 7 days, please follow up
directly via email — the maintainer is solo and a missed report is more
likely than a deliberate non-response.

## Supported versions

Only the latest tagged release is actively patched. If you self-host,
update to the latest release before reporting; we may decline reports
against versions older than 6 months.

## Scope

In scope:
- Application code in this repository (`src/`, `web/`, `scripts/`).
- The default deployment configuration documented in `README.md` and
  `docs/deploy.md`.

Out of scope (report to the respective vendor):
- Third-party services this app talks to (Gemini API, FatSecret, Stripe,
  Strava, Fitbit, Resend).
- Cloudflare / GCP infrastructure providing the hosted instance.

## Hosted instance disclosure

The hosted instance at **`https://macro.anirudhtopiwala.com/macro_app/`**
is operated by the maintainer. For vulnerabilities affecting that
specific deployment (vs. the codebase), use the same channels above and
prefix the report subject `[hosted-instance]` so we route it as an
incident, not a code patch.

We do not currently run a paid bug-bounty program. We will credit you in
release notes if you'd like, and we will not pursue legal action
against good-faith research that:

- stays within your own account,
- does not access other users' data,
- does not run automated scanners that materially impact availability,
- gives us a reasonable disclosure window before going public.

## Self-hosting hardening checklist

If you operate your own MacroShot instance you take on full operator
responsibility for security. Before exposing it to anyone but yourself:

- **`JWT_SECRET`** — required. Set to a high-entropy value
  (`openssl rand -hex 32`). The app refuses to start in production
  with the default.
- **`OAUTH_TOKEN_KEY`** — required if you enable Strava / Fitbit / Oura.
  Fernet key for encrypting OAuth refresh tokens at rest. See
  [`docs/deploy.md`](docs/deploy.md) for generation.
- **`APP_ENV=production`** — enables stricter CORS, disables API docs,
  turns on full security headers.
- **`chmod 600 .env`** — keys leak to other users on a shared box at
  the default 644.
- **TLS reverse proxy** (Caddy / nginx / Cloudflare) in front of
  uvicorn. Never expose uvicorn directly.
- **Run unprivileged + sandbox the systemd unit.** See
  [`docs/deploy.md`](docs/deploy.md) for the bundled unit template.
- **Privacy / legal:** running a public instance? Replace the bundled
  Privacy Policy + Terms to name yourself as data controller — see
  [`docs/self-hosting-legal.md`](docs/self-hosting-legal.md).

## Disclosure

Please give us a reasonable window (typically 30–90 days) to ship a fix
before public disclosure. We will coordinate timing with you and credit
you in the release notes if you'd like.
