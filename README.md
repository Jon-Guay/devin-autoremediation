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
4. **Devin** receives the task, investigates the issue, implements a fix, opens a pull request (with `Closes #N` in the description), and closes the fork issue directly — no human intervention required.
5. **Devin Exporter** exposes Prometheus metrics (session stats, PR counts, ACU consumption) for the Grafana Cloud dashboard.

## Prerequisites

- Docker + Docker Compose
- GitHub personal access token (`repo` and `read:user` scopes) — `read:user` is required by the Grafana GitHub datasource to query issue authors
- [Devin API key](https://app.devin.ai/settings/api) and your Devin org ID (format: `org-xxx`)
- [Anthropic API key](https://console.anthropic.com)
- [Grafana Cloud account](https://grafana.com/auth/sign-up) (free tier works)
- [ngrok account](https://ngrok.com) + authtoken (free tier works)
- Grafana service account token with Editor role — create at `https://<your-stack>.grafana.net` → Administration → Service accounts

## Setup

### 1. Fork apache/superset

Fork [apache/superset](https://github.com/apache/superset) to your GitHub account. Note your fork's full name (e.g. `jonguay/superset`).

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in all values. See `.env.example` for descriptions. Key values:

| Variable | Where to find it |
|----------|-----------------|
| `GITHUB_TOKEN` | github.com → Settings → Developer settings → Personal access tokens |
| `DEVIN_API_KEY` | app.devin.ai → Settings → API |
| `DEVIN_ORG_ID` | app.devin.ai → Settings → API (format: `org-xxx`) |
| `NGROK_AUTHTOKEN` | dashboard.ngrok.com → Your authtoken |
| `GRAFANA_URL` | Your Grafana Cloud stack URL (e.g. `https://yourstack.grafana.net`) |
| `GRAFANA_SA_TOKEN` | Grafana → Administration → Service accounts → Add token |

### 3. Configure Grafana Cloud data sources (one-time)

**Add Prometheus and Loki data sources:**
- Prometheus: use `GRAFANA_CLOUD_PROMETHEUS_URL` + username/API key from `.env`
- Loki: use `GRAFANA_CLOUD_LOKI_URL` + username/API key from `.env`

**Install and configure the GitHub datasource:**
- Grafana Cloud → Connections → Add new connection → search "GitHub"
- Install the plugin, add a datasource authenticated with your `GITHUB_TOKEN`

**Import dashboard:**
Import `observability/grafana/dashboard.json` into Grafana Cloud.

> **Note:** The alert rule ("Open Issues in Fork") and webhook contact point ("devin-autoremediation") are pre-configured in Grafana Cloud. The contact point URL is automatically updated with the live ngrok URL each time you run `docker compose up`.

### 4. Start the stack

```bash
docker compose up -d
```

This starts all services:
- `webhook_server` (port 8000) — receives Grafana alerts, triggers Devin
- `devin_exporter` (port 9090, localhost only) — Prometheus metrics
- `alloy` — ships logs and metrics to Grafana Cloud
- `ngrok` — creates a public HTTPS tunnel to `webhook_server`
- `grafana_updater` — reads the live ngrok URL and updates the Grafana webhook contact point automatically, then exits

## Running a Scan

```bash
docker compose --profile scan run issue_scanner
```

This fetches issues from `apache/superset`, scores them with Claude AI, and mirrors the top 5 to your fork. The Grafana Cloud GitHub datasource alert will detect the new open issues and fire within one evaluation cycle.

## Demo Flow (5 minutes)

1. `docker compose up -d` — stack starts, ngrok tunnel opens, Grafana contact point is updated automatically
2. Open Grafana Cloud dashboard — all metrics at zero, alert in Normal state
3. Run `docker compose --profile scan run issue_scanner`
4. Watch issues appear in fork — Grafana GitHub datasource detects open issue count > 0
5. Alert fires → webhook → webhook server triggers a Devin session per issue
6. Devin investigates, fixes the code, opens a PR with `Closes #N` in the description, then closes the fork issue directly
7. Issue count drops to zero → alert resolves
8. PR link and session status appear in the Grafana dashboard table panel

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

## Testing

Automated tests live under `tests/` and mock GitHub and Devin HTTP calls (no real tokens or network required for those cases). They target **`webhook_server`** only: `tests/conftest.py` sets dummy env vars and a temporary `SESSION_STORE_PATH`, and **`respx`** stubs outbound HTTP.

What they cover:

- **`GET /health`** returns OK.
- **`POST /webhook`** with no `firing` Grafana alerts does not call GitHub or Devin.
- Invalid JSON body yields **400**; invalid webhook signature (when a secret is enforced in the test) yields **401**.
- A **`firing`** alert triggers one mocked GitHub issues fetch and one mocked Devin session create; **`GET /sessions`** reflects the saved issue.
- A second webhook for the same issue does **not** create a second Devin session (idempotency via the session store).

Use a **virtual environment** so `pip` does not touch system Python (avoids `externally-managed-environment` on Debian/Ubuntu/WSL):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r webhook_server/requirements.txt -r requirements-dev.txt
python -m pytest -c tests/pytest.ini -v
```

If `python3 -m venv` fails, install the venv package (e.g. `sudo apt install python3-venv`) and retry.

Configuration lives in **`tests/pytest.ini`** (`pythonpath` points at `webhook_server/` so imports match the Docker layout). Using **`-c tests/pytest.ini`** sets pytest’s root to `tests/` so paths stay consistent from the repo root.

Alternatively, from **`tests/`**:

```bash
cd tests && python -m pytest -v
```

To run a single file or test from the repo root:

```bash
python -m pytest -c tests/pytest.ini tests/test_webhook.py -v
python -m pytest -c tests/pytest.ini tests/test_webhook.py::test_webhook_firing_triggers_devin_for_open_issues -v
```

## Troubleshooting

| Problem | Check |
|---------|-------|
| `externally-managed-environment` when using pip | Create and activate a **venv** (see **Testing**); do not install into system Python. |
| No metrics in Grafana | `curl localhost:9090/metrics` — check Alloy logs: `docker compose logs alloy` |
| Webhook not triggering | Check `docker compose logs grafana_updater` — the contact point URL may not have been set; verify ngrok is running with `docker compose logs ngrok` |
| Contact point URL is stale | Restart the stack: `docker compose up -d` — `grafana_updater` sets the URL fresh on every startup |
| GitHub datasource query errors | Verify `GITHUB_TOKEN` has both `repo` and `read:user` scopes |
| No issues mirrored | Check scanner logs: `docker compose --profile scan logs issue_scanner`; verify `GITHUB_TOKEN` has fork write access |
| Devin session not created | Verify `DEVIN_API_KEY` and `DEVIN_ORG_ID`; check `curl localhost:8000/sessions` |
