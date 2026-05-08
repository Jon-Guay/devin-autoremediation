import asyncio
import os
import time

import httpx
import structlog

log = structlog.get_logger()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_URL = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


async def get_open_issues(repo: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/search/issues",
            headers=HEADERS,
            params={"q": f"repo:{repo} is:issue is:open", "per_page": 100},
        )

        if r.status_code == 429 or r.status_code == 403:
            reset_at = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset_at - int(time.time()), 0) + 5
            log.warning("github_rate_limited", status=r.status_code, sleeping_seconds=wait)
            await asyncio.sleep(wait)
            # Return empty list rather than crashing the refresh loop;
            # the previous cached value stays in place.
            return []

        r.raise_for_status()
        return r.json().get("items", [])
