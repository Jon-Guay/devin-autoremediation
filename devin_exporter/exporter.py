import asyncio
import json
import os
import time as time_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
import structlog.stdlib
from dotenv import load_dotenv
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import GaugeMetricFamily

import devin_client
import github_client

load_dotenv()

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)

log = structlog.get_logger()

FORK_REPO = os.getenv("GITHUB_FORK_REPO", "")
SESSION_STORE_PATH = Path(os.getenv("SESSION_STORE_PATH", "/data/sessions.json"))
SCRAPE_PORT = int(os.getenv("EXPORTER_PORT", "9090"))
REFRESH_INTERVAL = int(os.getenv("EXPORTER_REFRESH_INTERVAL", "60"))
METRICS_WINDOW_DAYS = int(os.getenv("DEVIN_METRICS_WINDOW_DAYS", "30"))

TERMINAL_STATUSES = {"finished", "blocked", "expired"}


class _Cache:
    # v1 session list + local store
    devin_sessions: list[dict] = []
    open_issues: list[dict] = []
    known_sessions: list[dict] = []
    # v3 enterprise metrics (None = endpoint not accessible)
    usage_metrics: Optional[dict] = None
    session_metrics: Optional[dict] = None
    pr_metrics: Optional[dict] = None
    daily_consumption: Optional[dict] = None


_cache = _Cache()


def _load_known_sessions() -> list[dict]:
    if SESSION_STORE_PATH.exists():
        try:
            return json.loads(SESSION_STORE_PATH.read_text())
        except Exception:
            pass
    return []


def _metrics_window() -> tuple[int, int]:
    now = int(time_module.time())
    return now - (METRICS_WINDOW_DAYS * 86400), now


async def _refresh() -> None:
    try:
        time_after, time_before = _metrics_window()

        (
            devin_sessions,
            open_issues,
            usage_metrics,
            session_metrics,
            pr_metrics,
            daily_consumption,
        ) = await asyncio.gather(
            devin_client.list_sessions(),
            github_client.get_open_issues(FORK_REPO),
            devin_client.get_usage_metrics(time_after, time_before),
            devin_client.get_session_metrics(time_after, time_before),
            devin_client.get_pr_metrics(time_after, time_before),
            devin_client.get_daily_consumption(time_after, time_before),
        )
        known_sessions = await asyncio.to_thread(_load_known_sessions)

        _cache.devin_sessions = devin_sessions
        _cache.open_issues = open_issues
        _cache.known_sessions = known_sessions
        _cache.usage_metrics = usage_metrics
        _cache.session_metrics = session_metrics
        _cache.pr_metrics = pr_metrics
        _cache.daily_consumption = daily_consumption

        log.info(
            "cache_refreshed",
            devin_sessions=len(devin_sessions),
            open_issues=len(open_issues),
            known_sessions=len(known_sessions),
            v3_usage=usage_metrics is not None,
            v3_sessions=session_metrics is not None,
            v3_prs=pr_metrics is not None,
            v3_consumption=daily_consumption is not None,
        )
    except Exception as e:
        log.error("cache_refresh_failed", error=str(e))


class DevinCollector:
    def describe(self):
        return []

    def collect(self):
        yield from self._pipeline_metrics()
        yield from self._v3_usage_metrics()
        yield from self._v3_session_metrics()
        yield from self._v3_pr_metrics()
        yield from self._v3_consumption_metrics()

    # ── Pipeline metrics (derived from GitHub + sessions.json) ──────────────

    def _pipeline_metrics(self):
        known = _cache.known_sessions
        open_issues = _cache.open_issues
        devin_sessions = _cache.devin_sessions

        active_issue_numbers = {
            str(s["issue_number"])
            for s in known
            if s.get("status") not in TERMINAL_STATUSES
        }

        pending_family = GaugeMetricFamily(
            "devin_pending_issues_total",
            "Open fork issues without an active Devin session (observability only — alert trigger is GitHub datasource)",
            labels=["issue_number", "issue_url"],
        )
        for issue in open_issues:
            if str(issue["number"]) not in active_issue_numbers:
                pending_family.add_metric(
                    [str(issue["number"]), issue["html_url"]], 1
                )
        yield pending_family

        status_counts: dict[str, int] = {}
        for s in devin_sessions:
            st = s.get("status_enum", "unknown")
            status_counts[st] = status_counts.get(st, 0) + 1

        session_total = GaugeMetricFamily(
            "devin_session_total",
            "Devin sessions by status",
            labels=["status"],
        )
        for status, count in status_counts.items():
            session_total.add_metric([status], count)
        yield session_total

        duration_family = GaugeMetricFamily(
            "devin_session_duration_seconds",
            "Duration of tracked Devin sessions in seconds",
            labels=["session_id", "status"],
        )
        for rec in known:
            try:
                created = datetime.fromisoformat(
                    str(rec["created_at"]).replace("Z", "+00:00")
                )
                updated = datetime.fromisoformat(
                    str(rec["updated_at"]).replace("Z", "+00:00")
                )
                duration_family.add_metric(
                    [rec["session_id"], rec.get("status", "unknown")],
                    (updated - created).total_seconds(),
                )
            except Exception as e:
                log.warning("duration_metric_parse_failed", session_id=rec.get("session_id"), error=str(e))
        yield duration_family

        pr_count = sum(
            1 for rec in known
            if rec.get("pr_url") and rec.get("status") == "finished"
        )
        pr_family = GaugeMetricFamily(
            "devin_pr_created_total",
            "Devin sessions that produced a pull request",
        )
        pr_family.add_metric([], pr_count)
        yield pr_family

    # ── v3 usage metrics ─────────────────────────────────────────────────────

    def _v3_usage_metrics(self):
        data = _cache.usage_metrics
        if not data:
            return
        window = str(METRICS_WINDOW_DAYS)

        for metric_name, field, description in [
            ("devin_usage_sessions_count", "sessions_count", "Sessions started in window"),
            ("devin_usage_prs_created_count", "prs_created_count", "PRs created in window"),
            ("devin_usage_prs_merged_count", "prs_merged_count", "PRs merged in window"),
            ("devin_usage_searches_count", "searches_count", "Searches performed in window"),
        ]:
            if field in data:
                g = GaugeMetricFamily(
                    metric_name, f"{description} (window_days={window})"
                )
                g.add_metric([], float(data[field]))
                yield g

    # ── v3 session metrics ───────────────────────────────────────────────────

    def _v3_session_metrics(self):
        data = _cache.session_metrics
        if not data:
            return
        window = str(METRICS_WINDOW_DAYS)

        for metric_name, field, description in [
            ("devin_sessions_created_count", "sessions_created_count", "Sessions created in window"),
            ("devin_sessions_with_merged_prs_count", "sessions_with_merged_prs_count", "Sessions that produced a merged PR in window"),
            ("devin_avg_acus_per_session", "avg_acus_per_session", "Average ACUs consumed per session in window"),
        ]:
            if field in data:
                g = GaugeMetricFamily(
                    metric_name, f"{description} (window_days={window})"
                )
                g.add_metric([], float(data[field]))
                yield g

        if "sessions_created_by_size" in data:
            by_size = GaugeMetricFamily(
                "devin_sessions_by_size_count",
                f"Sessions created by size classification in window (window_days={window})",
                labels=["size"],
            )
            for size, count in data["sessions_created_by_size"].items():
                by_size.add_metric([size], float(count))
            yield by_size

        if "sessions_created_by_origin" in data:
            by_origin = GaugeMetricFamily(
                "devin_sessions_by_origin_count",
                f"Sessions created by origin in window (window_days={window})",
                labels=["origin"],
            )
            for origin, count in data["sessions_created_by_origin"].items():
                by_origin.add_metric([origin], float(count))
            yield by_origin

    # ── v3 PR metrics ────────────────────────────────────────────────────────

    def _v3_pr_metrics(self):
        data = _cache.pr_metrics
        if not data:
            return
        window = str(METRICS_WINDOW_DAYS)

        for metric_name, field, description in [
            ("devin_prs_created_count", "prs_created_count", "PRs created in window"),
            ("devin_prs_opened_count", "prs_opened_count", "PRs opened in window"),
            ("devin_prs_merged_count", "prs_merged_count", "PRs merged in window"),
            ("devin_prs_closed_count", "prs_closed_count", "PRs closed in window"),
        ]:
            if field in data:
                g = GaugeMetricFamily(
                    metric_name, f"{description} (window_days={window})"
                )
                g.add_metric([], float(data[field]))
                yield g

    # ── v3 consumption metrics ───────────────────────────────────────────────

    def _v3_consumption_metrics(self):
        data = _cache.daily_consumption
        if not data:
            return

        if "total_acus" in data:
            g = GaugeMetricFamily(
                "devin_acus_consumed_total",
                f"Total ACUs consumed in window (window_days={METRICS_WINDOW_DAYS})",
            )
            g.add_metric([], float(data["total_acus"]))
            yield g

        # Most recent day's per-product breakdown
        daily = data.get("consumption_by_date", [])
        if daily:
            latest = daily[-1]
            by_product = GaugeMetricFamily(
                "devin_acus_by_product_latest",
                "ACUs consumed by product on the most recent day",
                labels=["product"],
            )
            for product, acus in latest.get("acus_by_product", {}).items():
                if acus is not None:
                    by_product.add_metric([product], float(acus))
            yield by_product


async def _refresh_loop() -> None:
    while True:
        await _refresh()
        await asyncio.sleep(REFRESH_INTERVAL)


async def main() -> None:
    REGISTRY.register(DevinCollector())
    start_http_server(SCRAPE_PORT)
    log.info(
        "exporter_started",
        port=SCRAPE_PORT,
        fork_repo=FORK_REPO,
        metrics_window_days=METRICS_WINDOW_DAYS,
    )
    await _refresh_loop()


if __name__ == "__main__":
    asyncio.run(main())
