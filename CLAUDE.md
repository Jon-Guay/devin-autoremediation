# devin-autoremediation

Demo system that auto-remediates GitHub issues from `apache/superset` using Devin.ai, with end-to-end observability in Grafana Cloud.

## Architecture

```
[docker compose --profile scan run issue_scanner]
         │
         ▼
issue_scanner  ──── mirrors top 5 issues ────→  GitHub fork (GITHUB_FORK_REPO)
                                                         │
                                                         │ open issues detected
                                                         ▼
                                              Grafana Cloud
                                              GitHub datasource alert
                                              (open issue count > 0)
                                                         │
                                              webhook → ngrok → webhook_server:8000
                                                         │
                                                         │ fetches open issues from GitHub
                                                         │ cross-references session_store
                                                         ▼
                                              Devin.ai API (create session)
                                                         │
                                              session status/logs → Grafana Cloud
                                                         │
                                              devin_exporter:9090/metrics
                                              (observability only — not trigger path)
                                              Alloy → Grafana Cloud dashboard
```

## Directory Structure

```
devin-autoremediation/
├── issue_scanner/      # Run on demand: scans apache/superset, AI-filters, mirrors to fork
├── webhook_server/     # Always running: receives Grafana webhooks, triggers Devin sessions
├── devin_exporter/     # Always running: exposes Prometheus metrics for Devin + issues
├── observability/      # Alloy config + Grafana dashboard (details provided by user)
├── docker-compose.yml
├── .env.example
└── README.md
```

## Running the Stack

```bash
cp .env.example .env
# Fill in all values in .env

# Start long-running services
docker compose up -d

# Trigger an issue scan (run once or on demand)
docker compose --profile scan run issue_scanner

# Start ngrok to expose webhook_server
ngrok http 8000
# → update Grafana Cloud webhook contact point URL with the ngrok URL
```

## Key Environment Variables

| Variable | Description |
|----------|-------------|
| `GITHUB_TOKEN` | Personal access token with `repo` scope |
| `GITHUB_SOURCE_REPO` | Upstream repo to scan (default: `apache/superset`) |
| `GITHUB_FORK_REPO` | Your fork where issues are mirrored (e.g. `jonguay/superset`) |
| `ANTHROPIC_API_KEY` | Used by issue_scanner for AI semantic scoring |
| `DEVIN_API_KEY` | Devin.ai API key |
| `GRAFANA_CLOUD_*` | Grafana Cloud credentials for Alloy remote_write |
| `WEBHOOK_SECRET` | HMAC secret for Grafana webhook validation (leave empty to skip) |
| `SESSION_STORE_PATH` | Path to sessions JSON file (default: `/data/sessions.json`) |

## Service Endpoints

| Service | Endpoint | Description |
|---------|----------|-------------|
| webhook_server | `GET /health` | Health check |
| webhook_server | `GET /sessions` | All tracked Devin sessions |
| webhook_server | `POST /webhook` | Grafana Cloud alert webhook receiver |
| devin_exporter | `GET /metrics` | Prometheus metrics |

## Prometheus Metrics

| Metric | Description |
|--------|-------------|
| `devin_pending_issues_total{issue_number, issue_url}` | Open fork issues without an active Devin session — **alert trigger** (`sum(devin_pending_issues_total) > 0`) |
| `devin_session_total{status}` | Count of sessions by status (scoped to current run via sessions.json) |
| `devin_session_duration_seconds{session_id, title, status}` | Session duration |
| `devin_session_acus_consumed{session_id, title, status}` | ACUs consumed per session |
| `devin_session_status_info{session_id, title, status, status_detail}` | Info gauge (value=1) for current session state |
| `devin_session_pr_info{session_id, title, issue_url, pr_url, status}` | Info gauge (value=1) for sessions with a PR |
| `devin_pr_created_total` | Total sessions that produced a PR |

Note: GitHub datasource cannot be used as the alert trigger — it returns long-format data which Grafana SSE alerting rejects. Alert uses `devin_pending_issues_total` via Prometheus instead.

## Grafana Cloud Alert Setup (one-time manual)

1. Add Prometheus datasource using `GRAFANA_CLOUD_PROMETHEUS_URL` + credentials
2. Add Loki datasource using `GRAFANA_CLOUD_LOKI_URL` + credentials
3. Create alert rule:
   - Data source: Prometheus
   - Query: `sum(devin_pending_issues_total)`
   - Condition: value > 0
4. Create webhook contact point with URL: `https://<ngrok-id>.ngrok.io/webhook` (auto-patched by grafana_updater on startup)

## Development Notes

- All services use `structlog` with JSON output to stdout — Alloy tails these for Loki
- `session-data` Docker volume is shared between `webhook_server` (writes) and `devin_exporter` (reads)
- `devin_client.py` and `github_client.py` are intentionally duplicated per service (no shared package) for simplicity
- Devin API base URL: `https://api.devin.ai/v3`
- Session endpoints are org-scoped: `/organizations/{DEVIN_ORG_ID}/sessions`
- Auth: `Authorization: Bearer {DEVIN_API_KEY}` (service user key, prefix `cog_`)
- `DEVIN_ORG_ID` env var required (format: `org-xxx`)
- v3 session `status` field values: `new`, `claimed`, `running`, `exit` (terminal), `error` (terminal), `suspended` (terminal), `resuming`
- Polling model: poll `GET /organizations/{org_id}/sessions/{id}` every 10 seconds until `exit`, `error`, or `suspended`
- `pull_requests` is an array in v3 (was `pull_request` object in v1); idempotency flag removed from create body
- Issue scanner idempotency: checks fork for `Source: {upstream_url}` in body before mirroring
- Webhook idempotency: checks session_store before creating a new Devin session
