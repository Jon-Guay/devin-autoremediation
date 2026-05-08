import asyncio
import os
import time

import httpx
import structlog

log = structlog.get_logger()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_URL = "https://api.github.com"

_http = httpx.AsyncClient(
    timeout=30,
    headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    },
)


async def get_open_issues(repo: str) -> list[dict]:
    all_items: list[dict] = []
    page = 1

    while True:
        r = await _http.get(
            f"{BASE_URL}/search/issues",
            params={"q": f"repo:{repo} is:issue is:open", "per_page": 100, "page": page},
        )

        if r.status_code in (429, 403):
            reset_at = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset_at - int(time.time()), 0) + 5
            log.warning("github_rate_limited", status=r.status_code, sleeping_seconds=wait)
            await asyncio.sleep(wait)
            return all_items  # return cached partial result; retry on next refresh cycle

        r.raise_for_status()
        items = r.json().get("items", [])
        all_items.extend(items)

        if len(items) < 100:
            break
        page += 1

    return all_items
