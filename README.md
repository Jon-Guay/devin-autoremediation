# devin-autoremediation

An event-driven pipeline that automatically identifies good candidate issues from [apache/superset](https://github.com/apache/superset), mirrors them to a fork, triggers [Devin.ai](https://devin.ai) to fix them, and visualizes the entire workflow in Grafana Cloud.

## Architecture

```
Issue Scanner → GitHub Fork → Grafana GitHub datasource alert
                                          ↓
                             Webhook → webhook_server
                                          ↓  fetches open issues from GitHub
                                          ↓  cross-references session store
                                      Devin.ai API
                                          ↓
                             Status/Logs → Grafana Cloud
```

1. **Issue Scanner** scans `apache/superset` for good candidate issues using a two-pass filter: keyword/label matching, then Claude AI semantic scoring. Top issues are mirrored to your fork.
2. **Grafana Cloud** monitors the fork via the GitHub datasource. When open issues are detected, an alert fires a webhook.
3. **Webhook Server** receives the alert, fetches open issues from GitHub directly, and calls the Devin API for any issues not already being handled.
4. **Devin Exporter** exposes Prometheus metrics (session stats, PR counts, ACU consumption) for the Grafana Cloud dashboard.

## Prerequisites

- Docker + Docker Compose
- GitHub personal access token (`repo` scope)
- [Devin API key](https://app.devin.ai/settings/api)
- [Anthropic API key](https://console.anthropic.com)
- [Grafana Cloud account](https://grafana.com/auth/sign-up) (free tier works)
- [ngrok account](https://ngrok.com) + authtoken

## Setup

### 1. Fork apache/superset

Fork [apache/superset](https://github.com/apache/superset) to your GitHub account. Note your fork's full name (e.g. `jonguay/superset`).

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in all values. See `.env.example` for descriptions.

### 3. Start the stack

```bash
docker compose up -d
```

This starts `webhook_server` (port 8000), `devin_exporter` (port 9090), and `alloy`.

### 4. Expose the webhook server with ngrok

```bash
ngrok http 8000
```

Note the HTTPS URL (e.g. `https://abc123.ngrok.io`).

### 5. Configure Grafana Cloud

**Add data sources:**
- Prometheus: use `GRAFANA_CLOUD_PROMETHEUS_URL` + username/API key from `.env`
- Loki: use `GRAFANA_CLOUD_LOKI_URL` + username/API key from `.env`

**Install and configure the GitHub datasource:**
- In Grafana Cloud, go to Connections → Add new connection → search "GitHub"
- Install the GitHub datasource plugin
- Add a new GitHub datasource, authenticated with your `GITHUB_TOKEN`

**Create alert rule:**
- Data source: GitHub
- Query: Issues in `GITHUB_FORK_REPO`, state = open, reduce to Count
- Condition: count > 0
- Contact point: Webhook type, URL = `https://<your-ngrok-url>/webhook`

**Import dashboard:**  
Import `observability/grafana/dashboard.json` into Grafana Cloud.

## Running a Scan

```bash
docker compose --profile scan run issue_scanner
```

This fetches issues from `apache/superset`, scores them with Claude AI, and mirrors the top 5 to your fork. The Grafana Cloud GitHub datasource alert will detect the new open issues and fire within one evaluation cycle.

## Demo Flow (5 minutes)

1. Open Grafana Cloud dashboard — all metrics at zero
2. Run `docker compose --profile scan run issue_scanner`
3. Watch issues appear in fork — Grafana GitHub datasource detects them
4. Grafana alert fires → webhook → webhook server fetches issues → triggers Devin
5. Session status changes to `working` in the table panel
6. Show structured pipeline logs in the Loki panel
7. When session finishes: PR link appears in the table

## Checking Status

```bash
# Health check
curl localhost:8000/health

# View all tracked Devin sessions
curl localhost:8000/sessions | jq

# View raw Prometheus metrics
curl localhost:9090/metrics

# View logs
docker compose logs -f webhook_server
docker compose logs -f devin_exporter
```

## Troubleshooting

| Problem | Check |
|---------|-------|
| No metrics in Grafana | `curl localhost:9090/metrics` — check Alloy logs: `docker compose logs alloy` |
| Webhook not triggering | Verify ngrok URL in Grafana contact point; check `docker compose logs webhook_server` |
| No issues mirrored | Check scanner logs: `docker compose --profile scan logs issue_scanner`; verify `GITHUB_TOKEN` has fork write access |
| Devin session not created | Verify `DEVIN_API_KEY`; check `curl localhost:8000/sessions` |
