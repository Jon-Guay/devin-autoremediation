# devin-autoremediation

An event-driven pipeline that automatically identifies good candidate issues from [apache/superset](https://github.com/apache/superset), mirrors them to a fork, triggers [Devin.ai](https://devin.ai) to fix them, and visualizes the entire workflow in Grafana Cloud.

## Architecture

```
Issue Scanner → GitHub Fork → Prometheus Metrics → Grafana Cloud Alert
                                                           ↓
                                              Webhook → webhook_server
                                                           ↓
                                                      Devin.ai API
                                                           ↓
                                              Status/Logs → Grafana Cloud
```

1. **Issue Scanner** scans `apache/superset` for good candidate issues using a two-pass filter: keyword/label matching, then Claude AI semantic scoring. Top issues are mirrored to your fork.
2. **Devin Exporter** exposes `devin_pending_issues_total` as a Prometheus metric. When > 0, a Grafana Cloud alert fires.
3. **Webhook Server** receives the Grafana alert and calls the Devin API to start a remediation session.
4. **Grafana Cloud** dashboards show session status, logs, and PR links in real time.

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

**Create alert rule:**
- Query: `devin_pending_issues_total > 0`
- Add labels: `issue_number = {{ $labels.issue_number }}` and `issue_url = {{ $labels.issue_url }}`
- Contact point: Webhook type, URL = `https://<your-ngrok-url>/webhook`

**Import dashboard:**  
Import `observability/grafana/dashboard.json` into Grafana Cloud.

## Running a Scan

```bash
docker compose --profile scan run issue_scanner
```

This fetches issues from `apache/superset`, scores them with Claude AI, and mirrors the top 5 to your fork. The exporter will detect the new open issues and the Grafana alert will fire within ~1 minute.

## Demo Flow (5 minutes)

1. Open Grafana Cloud dashboard — all metrics at zero
2. Run `docker compose --profile scan run issue_scanner`
3. Watch `devin_pending_issues_total` rise in the dashboard
4. Grafana alert fires → webhook triggers Devin
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
