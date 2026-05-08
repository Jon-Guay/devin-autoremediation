import asyncio
import os
import re
import time
from datetime import datetime

import httpx
import structlog

from models import RawIssue

log = structlog.get_logger()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_URL = "https://api.github.com"
HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def _guard_rate_limit(response: httpx.Response) -> None:
    remaining = int(response.headers.get("X-RateLimit-Remaining", 100))
    if remaining < 10:
        reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
        wait = max(reset_at - int(time.time()), 0) + 5
        log.warning("rate_limit_low", remaining=remaining, sleeping_seconds=wait)
        await asyncio.sleep(wait)


async def get_candidate_issues(
    repo: str,
    labels: list[str],
    keywords: list[str],
    max_results: int = 100,
) -> list[RawIssue]:
    issues_by_number: dict[int, RawIssue] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for label in labels:
            page = 1
            while True:
                r = await client.get(
                    f"{BASE_URL}/repos/{repo}/issues",
                    headers=HEADERS,
                    params={"state": "open", "labels": label, "per_page": 100, "page": page},
                )
                r.raise_for_status()
                await _guard_rate_limit(r)

                data = r.json()
                if not data:
                    break

                for item in data:
                    if "pull_request" in item:
                        continue
                    num = item["number"]
                    if num not in issues_by_number:
                        issues_by_number[num] = RawIssue(
                            number=num,
                            title=item["title"],
                            body=item.get("body") or "",
                            labels=[lb["name"] for lb in item.get("labels", [])],
                            html_url=item["html_url"],
                            created_at=datetime.fromisoformat(
                                item["created_at"].replace("Z", "+00:00")
                            ),
                        )

                if len(data) < 100:
                    break
                page += 1

    keyword_pattern = re.compile(
        "|".join(re.escape(k) for k in keywords), re.IGNORECASE
    )
    filtered = [
        issue
        for issue in issues_by_number.values()
        if keyword_pattern.search(issue.title) or keyword_pattern.search(issue.body[:1000])
    ]

    filtered.sort(key=lambda x: x.created_at, reverse=True)
    result = filtered[:max_results]
    log.info("candidate_issues_found", count=len(result))
    return result


async def create_issue(repo: str, title: str, body: str, labels: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/repos/{repo}/issues",
            headers=HEADERS,
            json={"title": title, "body": body, "labels": labels},
        )
        r.raise_for_status()
        return r.json()


async def search_issues_in_repo(repo: str, query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/search/issues",
            headers=HEADERS,
            params={"q": f"repo:{repo} {query}", "per_page": 5},
        )
        r.raise_for_status()
        return r.json().get("items", [])
