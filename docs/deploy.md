# Self-hosting MacroShot

This guide covers a basic Linux deployment using systemd + a reverse
proxy. It does not assume any specific cloud provider.

## Requirements

- A Linux server (any distro with systemd)
- Python 3.10+
- Node.js 18+ and npm
- A reverse proxy (Caddy or nginx)
- A domain name pointed at the server, with TLS (Let's Encrypt or
  similar)
- A [Google Gemini API key](https://ai.google.dev/)

## 1. Clone and install

```bash
sudo useradd -m -s /bin/bash macroshot
sudo -u macroshot -i

git clone https://github.com/anirudhtopiwala/macroshot.git
cd macroshot

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd web
npm install
npx vite build
cd ..
```

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env`. Required:

- `GEMINI_API_KEY` - your Gemini API key
- `JWT_SECRET` - a long random string (e.g. `openssl rand -hex 32`)

### Login PIN delivery

MacroShot uses 6-digit email PINs for login. The bundled `.env.example`
sets `DEBUG_SHOW_PINS=1`, so when Resend is **not** configured the PIN
is printed to the uvicorn server log (stderr) on signup / login. That
is fine for personal / single-operator instances — you read the PIN
out of `journalctl -u macroshot.service` and paste it.

For any instance with users other than yourself, configure Resend so
PINs reach users by email:

- `RESEND_API_KEY` - [Resend](https://resend.com/) API key
- `RESEND_FROM_EMAIL` - verified sender address (e.g. `noreply@your-domain.com`)
- **Remove `DEBUG_SHOW_PINS`** from `.env` so PINs stop appearing in logs.

Recommended for a public deployment:

- `OPERATOR_NAME`, `OPERATOR_FIRST_NAME`, `CONTACT_EMAIL` - shown in
  the in-app Privacy / Terms pages
- `DOMAIN`, `APP_URL` - your public URL
- `GITHUB_URL`, `SPONSOR_URL`, `DONATE_URL` - optional links shown in
  the app's About section

For self-host mode (single-user, all features unlocked, no billing
gates):

- Set `APP_MODE=self`
- Omit `STRIPE_SECRET_KEY`

See `.env.example` for the full list with comments.

### Upstream free-tier limits

Both Gemini and Resend have free tiers that will rate-limit or reject
requests before the app does. If meals stop analyzing or PIN emails
stop arriving for no obvious reason, check the upstream dashboard
first — what looks like an app bug is usually a quota hit:

- Gemini API — see [ai.google.dev/pricing](https://ai.google.dev/pricing)
  for current rate limits and the free-tier cap.
- Resend — see [resend.com/pricing](https://resend.com/pricing) for
  the current daily-send cap on the free tier.

For a personal or family instance the free tiers are usually plenty;
for anything wider, budget for a paid tier on both services.

## 3. systemd unit

Copy `docs/systemd.service.example` to `/etc/systemd/system/macroshot.service`
and substitute `{APP_USER}` (e.g. `macroshot`) and `{APP_ROOT}`
(e.g. `/home/macroshot/macroshot`):

```bash
sudo cp docs/systemd.service.example /etc/systemd/system/macroshot.service
sudo sed -i \
  -e 's|{APP_USER}|macroshot|' \
  -e 's|{APP_ROOT}|/home/macroshot/macroshot|g' \
  /etc/systemd/system/macroshot.service

sudo systemctl daemon-reload
sudo systemctl enable --now macroshot.service
sudo systemctl status macroshot.service
```

The service binds to `127.0.0.1:8000`. The reverse proxy in front of it
handles TLS and the public hostname.

> **Single worker only.** The app's in-memory rate limits, budget gate,
> and Gemini concurrency caps assume one process. The bundled systemd
> unit deliberately omits `--workers`, and the app refuses to start if
> `UVICORN_WORKERS` or `WEB_CONCURRENCY` is set to anything other than
> `1`. To scale, put more instances behind your reverse proxy with
> separate databases — don't raise the worker count.

## 4. Reverse proxy

### Caddy (simplest)

```Caddyfile
your-domain.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy handles TLS automatically via Let's Encrypt.

### nginx

Standard reverse proxy with `proxy_pass http://127.0.0.1:8000;`,
plus your usual TLS setup (e.g. `certbot --nginx`).

## 5. Verify

First, the health endpoint:

```bash
curl https://your-domain.example.com/macro_app/api/health
```

Expect `{"status":"ok","service":"macro_web"}`.

Then verify the full auth path end-to-end:

1. Open `https://your-domain.example.com/macro_app/` in a browser.
   You should see the login screen.
2. Enter your email and request a PIN.
3. If Resend is configured, the 6-digit PIN arrives by email. If you
   left `DEBUG_SHOW_PINS=1` for a personal instance, the PIN appears
   in `journalctl -u macroshot.service` instead.
4. Paste the PIN. You should land on the empty dashboard.

If the login screen renders but no PIN ever appears (neither in email
nor in the journal), either `RESEND_API_KEY` / `RESEND_FROM_EMAIL` is
wrong or `DEBUG_SHOW_PINS` is unset — pick one and retry.

## 6. Backups

The app uses SQLite in WAL journal mode. **Don't back up with `cp`** —
the `.db` file alone may be missing recent writes still in the WAL
sidecar. Use SQLite's `.backup` API instead:

```bash
sqlite3 ~/macroshot/macro_app.db ".backup ~/backups/macro_app.db"
```

Wrap that in cron + your usual rotation / off-site upload.

## 7. Updates

```bash
cd ~/macroshot
git pull
.venv/bin/pip install -r requirements.txt
cd web && npm install && npx vite build && cd ..
sudo systemctl restart macroshot.service
```

## 8. OAuth token key

`OAUTH_TOKEN_KEY` is the Fernet key that encrypts third-party OAuth
refresh tokens (Strava, Fitbit, Oura) at rest. Required if you enable
any of those integrations. Generate one with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set it once in `.env` and don't change it — rotating the key strands
every stored token.
