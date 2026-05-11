import os
from typing import Optional

import httpx

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]
ORG_ID = os.environ["DEVIN_ORG_ID"]
BASE_URL = "https://api.devin.ai/v3"

_http = httpx.AsyncClient(
    timeout=30,
    headers={"Authorization": f"Bearer {DEVIN_API_KEY}"},
)


async def list_sessions() -> list[dict]:
    r = await _http.get(f"{BASE_URL}/organizations/{ORG_ID}/sessions")
    r.raise_for_status()
    data = r.json()
    return data.get("sessions", data) if isinstance(data, dict) else data


async def _v3_get(path: str, params: dict) -> Optional[dict]:
    """GET a v3 enterprise endpoint; returns None on 401/403 (insufficient permissions)."""
    r = await _http.get(f"{BASE_URL}/{path}", params=params)
    if r.status_code in (401, 403):
        return None
    r.raise_for_status()
    return r.json()


async def get_usage_metrics(time_after: int, time_before: int) -> Optional[dict]:
    """sessions_count, prs_created_count, prs_merged_count, searches_count."""
    return await _v3_get(
        "enterprise/metrics/usage",
        {"time_after": time_after, "time_before": time_before},
    )


async def get_session_metrics(time_after: int, time_before: int) -> Optional[dict]:
    """sessions_created_count, sessions_with_merged_prs_count, avg_acus_per_session,
    sessions_created_by_size, sessions_created_by_origin."""
    return await _v3_get(
        "enterprise/metrics/sessions",
        {"time_after": time_after, "time_before": time_before},
    )


async def get_pr_metrics(time_after: int, time_before: int) -> Optional[dict]:
    """prs_created_count, prs_opened_count, prs_merged_count, prs_closed_count."""
    return await _v3_get(
        "enterprise/metrics/prs",
        {"time_after": time_after, "time_before": time_before},
    )


async def get_daily_consumption(time_after: int, time_before: int) -> Optional[dict]:
    """total_acus, consumption_by_date with per-product breakdown
    (devin, cascade, terminal, review)."""
    return await _v3_get(
        "enterprise/consumption/daily",
        {"time_after": time_after, "time_before": time_before},
    )
