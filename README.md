# home-dashboard

Single-screen operator console for the trading stack. Runs as one container alongside the existing 24-container stack on ai-primary, accessible via Tailscale at `http://ai-primary:8090`.

## What it shows

- Working capital + 24h/7d/30d/all-time PnL
- Win rate with W/L breakdown
- Per-strategy cards: Polymarket Compound + Liquidation Bot
- 4-flag gate visualization for liquidation lane
- Cumulative PnL chart (Chart.js)
- Recommended next actions (top 3)
- Open positions list
- System health strip (CL Streams, Pyth, Containers, OCDE, Subgraph)
- Master kill switch + per-strategy halt/pause

## Routes

| Path | Method | Purpose |
|---|---|---|
| `/` | GET | Static dashboard (single HTML) |
| `/api/health` | GET | Liveness probe |
| `/api/state` | GET | Full snapshot for the dashboard |
| `/api/positions` | GET | Detailed positions list |
| `/api/kill/all` | POST + `X-Confirm: HALT` | Master halt |
| `/api/kill/<strategy>` | POST + `X-Confirm: PAUSE-<id>` | Per-strategy halt |
| `/api/pause/<strategy>` | POST + `X-Confirm: PAUSE-<id>` | Per-strategy pause |
| `/api/stream` | GET | SSE activity feed |

## Phases

- **Phase 1 (this build)**: skeleton + mock data + concept-B styling
- **Phase 2**: wire to Redis (oracle prices), Postgres (positions), strategy-runners + liquidation-bot internal endpoints
- **Phase 3**: real SSE feed from Redis pub/sub (`exec.*` / `sig.*` / `warn.*` / `err.*`)
- **Phase 4**: kill switches actually disarm via Redis halt keys + OMS Gateway control channel
- **Phase 5**: range selector on chart, polish, mobile fallback

## Run locally

```bash
cd /Users/benedict/home-dashboard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
python -m app.main
# open http://localhost:8090
```

Tests:
```bash
pytest tests/ -q
```

## Deploy on ai-primary

```bash
# Build + push image (one time)
docker build -t ghcr.io/1305a001-ctrl/home-dashboard:latest .
docker push ghcr.io/1305a001-ctrl/home-dashboard:latest

# On ai-primary
ssh ai-primary 'sudo mkdir -p /srv/compose/home-dashboard /var/log/home-dashboard && sudo chown benadmin /srv/compose/home-dashboard'
scp docker/docker-compose.snippet.yml ai-primary:/srv/compose/home-dashboard/docker-compose.yml
ssh ai-primary 'cd /srv/compose/home-dashboard && sudo docker compose pull && sudo docker compose up -d'
```

Access via Tailscale at `http://ai-primary:8090` (or the ai-primary Tailscale IPv4).

## Tailscale + Chrome — yes, just works

If your Mac is on the same Tailscale tailnet as ai-primary:
1. `tailscale up` on the Mac (one-time)
2. Browse to `http://ai-primary:8090` in Chrome
3. Done — no VPN config, no port-forwarding, no public exposure

The container binds `0.0.0.0:8090` inside the docker network. Host firewall rules on ai-primary restrict that port to the Tailscale interface, so only tailnet-authenticated devices reach it.
