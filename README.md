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

## License

MIT
