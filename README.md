# Dexcom Dashboard

A small self-hosted glucose dashboard that polls **Dexcom Share** every 5 minutes (the same feed the Dexcom Follow app uses), stores readings in SQLite, and serves a phone-friendly dashboard with:

- current reading, trend arrow, delta and age, color-coded low / in-range / high / urgent
- 3h / 6h / 12h / 24h / 7d history chart with target band and hover/touch crosshair
- time-in-range, average, GMI (estimated A1c), variability (CV), lows/highs, readings table
- stale-data and polling-failure warnings, optional browser notifications
- mg/dL ↔ mmol/L toggle, dark/light theme

**Live demo (sample data only):** https://linuxexpert1.github.io/dexcom-dashboard/

> This is a personal tool, not a medical device. Always follow the Dexcom app and your care team for treatment decisions.

## How it works

```
Dexcom sensor → Dexcom cloud (Share) ──pydexcom──▶ app/poller.py ──▶ SQLite (data/glucose.db)
                                                                         │
                                          browser ◀── docs/index.html ◀── app/main.py (FastAPI: /api/*)
```

- `app/poller.py` — background thread; logs in with your Share credentials, pulls new readings, backs off on errors. `DEMO_MODE=1` generates realistic sample data instead.
- `app/main.py` — FastAPI app. Serves `docs/index.html` at `/` and a JSON API (`/api/current`, `/api/readings?hours=24`, `/api/stats?hours=24`, `/api/health`, `POST /api/poll`). Optional bearer-token auth.
- `docs/index.html` — the whole front end, one file, no build step and no external dependencies. The same file is published to GitHub Pages, where it runs in demo mode because there is no backend.

Credentials and data never leave the machine running the server. Only the code is on GitHub.

## Run it

Requires Python 3.10+.

```bash
git clone https://github.com/linuxexpert1/dexcom-dashboard.git
cd dexcom-dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `DEXCOM_USERNAME` (email or `+1XXXXXXXXXX` phone) and `DEXCOM_PASSWORD` — the **Share** account that the Dexcom app on the patient's phone is signed in to (the account that *shares*, not a follower). The Dexcom app must have Share turned on with at least one follower.
- `DEXCOM_REGION` — `us`, `ous` (outside US), or `jp`.
- `API_TOKEN` — set one if the dashboard will be reachable beyond your LAN; open `http://host:8080/?token=YOUR_TOKEN` once and the browser remembers it.

Then:

```bash
python -m app.main
# or: uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open http://localhost:8080 . To try it without Dexcom, set `DEMO_MODE=1` in `.env`.

Quick checks:

```bash
curl -s localhost:8080/api/health | python3 -m json.tool
curl -s localhost:8080/api/current
python -m pytest -q
```

## Option B: poll from GitHub Actions (no server, encrypted)

A scheduled workflow (`.github/workflows/poll.yml`) logs into Dexcom Share every ~5 minutes using repo **Actions Secrets** and publishes an **encrypted** file, `readings.enc.json`, to the repo's `data` branch. The readings are encrypted on the runner with AES-256-GCM (key derived from `DATA_PASSPHRASE` via PBKDF2-HMAC-SHA256, 200k iterations). The GitHub Pages dashboard shows a login screen; the password you enter is the key that decrypts the data **in your browser** (WebCrypto). So even though the file sits on a public URL, it's ciphertext — unreadable without the passphrase — and the passphrase never travels over the network.

Set the secrets once:

```bash
gh secret set DEXCOM_USERNAME --body '+1XXXXXXXXXX'   # the sensor-phone (sharer) Dexcom login
gh secret set DEXCOM_PASSWORD                          # prompts; not stored in shell history
gh secret set DEXCOM_REGION   --body 'us'
gh secret set DATA_PASSPHRASE                          # the family password used to log into the dashboard
gh workflow run "Poll Dexcom"                          # kick off the first run now
```

The dashboard login username is `bahal-family` (a label); the password is the `DATA_PASSPHRASE` you set above. To change the password, update the `DATA_PASSPHRASE` secret and re-run the workflow — the next published file is re-encrypted with the new key, and everyone re-enters the new password.

Trade-offs to know:

- **Encrypted, but strength = your passphrase.** The file is unreadable without the passphrase, so use a strong one. Anyone you give the password to can read the data; there's no per-person access or revocation short of rotating the passphrase.
- **Best-effort timing.** GitHub often runs scheduled workflows several minutes late and may skip runs under load, so this is not a real-time feed. The dashboard shows a "stale" banner whenever the newest reading is old.
- Dexcom credentials and the passphrase live only in encrypted Actions Secrets — never in the repo. Plaintext readings never leave the runner.
- For server-enforced access instead of client-side decryption (real per-user logins, revocation), run the backend (Option A) on a home box or an OCI free-tier VM.

## Run as a service (Linux, systemd)

```bash
sudo cp deploy/dexcom-dashboard.service /etc/systemd/system/
# edit User= and WorkingDirectory= to match where you cloned it
sudo systemctl daemon-reload && sudo systemctl enable --now dexcom-dashboard
journalctl -u dexcom-dashboard -f
```

Or with Ansible against a RHEL/OEL/Ubuntu host:

```bash
ansible-playbook -i "myhost," deploy/ansible/playbook.yml \
  -e dexcom_username='you@example.com' -e dexcom_password='...' -e api_token='...'
```

## Run on a Mac at login (launchd)

```bash
cp deploy/com.linuxexpert1.dexcom-dashboard.plist ~/Library/LaunchAgents/
# edit the paths inside to match your clone and venv
launchctl load ~/Library/LaunchAgents/com.linuxexpert1.dexcom-dashboard.plist
```

## Viewing from a phone / away from home

The dashboard is a plain web page. Point the phone at `http://<server-ip>:8080` on the LAN, or expose it through Tailscale / WireGuard / a reverse proxy with TLS. If you expose it, set `API_TOKEN`. You can also host `docs/index.html` anywhere and point it at a backend with `?api=https://your-backend`.

## Troubleshooting

- **`AccountError` / `SessionError` in the log** — wrong Share username/password/region, or Share isn't enabled in the Dexcom app. Test with `python -c "from pydexcom import Dexcom; print(Dexcom(username='..', password='..').get_current_glucose_reading())"`.
- **Stale banner** — sensor warm-up, phone away from the sensor, or the Dexcom app is not uploading. Check the Follow app shows the same gap.
- **Readings 5–10 minutes behind** — normal; Dexcom Share publishes with a short delay and the poller checks ~30 s after each expected reading.

---

## Author

### Abdihakim Hersi

Developed & designed by **Abdihakim Hersi**.

|          |                                                        |
| -------- | ------------------------------------------------------ |
| **Author** | **Abdihakim Hersi**                                  |
| **Email**  | [linuxexpert1@gmail.com](mailto:linuxexpert1@gmail.com) |

> [!NOTE]
> 💙 **Dedicated with love to his son Rahim Hersi.**
