#!/usr/bin/env python3
"""
Demo reset: closes mirrored issues, closes Devin's PRs, deletes their branches,
and clears the session store. Run between demo recordings for a clean slate.

    docker compose --profile reset run demo_reset
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
import structlog
from dotenv import load_dotenv

load_dotenv()

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
FORK_REPO = os.environ["GITHUB_FORK_REPO"]
SESSION_STORE_PATH = os.getenv("SESSION_STORE_PATH", "/data/sessions.json")
SOURCE_MARKER = "**Source:** https://github.com/apache/superset"

_gh = httpx.AsyncClient(
    timeout=30,
    headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    },
)


async def get_mirrored_issues() -> list[dict]:
    issues, page = [], 1
    while True:
        r = await _gh.get(
            f"https://api.github.com/repos/{FORK_REPO}/issues",
            params={"state": "all", "per_page": 100, "page": page},
        )
        r.raise_for_status()
        batch = r.json()
        for item in batch:
            if "pull_request" not in item and SOURCE_MARKER in (item.get("body") or ""):
                issues.append(item)
        if len(batch) < 100:
            break
        page += 1
    return issues


async def close_issue(number: int, title: str) -> None:
    r = await _gh.patch(
        f"https://api.github.com/repos/{FORK_REPO}/issues/{number}",
        json={"state": "closed"},
    )
    r.raise_for_status()
    log.info("issue_closed", number=number, title=title)


async def get_open_prs() -> list[dict]:
    r = await _gh.get(
        f"https://api.github.com/repos/{FORK_REPO}/pulls",
        params={"state": "open", "per_page": 100},
    )
    r.raise_for_status()
    return r.json()


async def close_pr_and_delete_branch(pr: dict) -> None:
    number = pr["number"]
    branch = pr["head"]["ref"]
    title = pr["title"]

    r = await _gh.patch(
        f"https://api.github.com/repos/{FORK_REPO}/pulls/{number}",
        json={"state": "closed"},
    )
    r.raise_for_status()
    log.info("pr_closed", number=number, title=title)

    r = await _gh.delete(
        f"https://api.github.com/repos/{FORK_REPO}/git/refs/heads/{branch}",
    )
    if r.status_code in (204, 422):
        log.info("branch_deleted", branch=branch)
    else:
        r.raise_for_status()


def clear_session_store() -> None:
    path = Path(SESSION_STORE_PATH)
    if path.exists():
        path.write_text("{}")
        log.info("session_store_cleared", path=str(path))
    else:
        log.info("session_store_not_found_skipping", path=str(path))


async def main() -> None:
    log.info("demo_reset_start", repo=FORK_REPO)

    issues = await get_mirrored_issues()
    log.info("mirrored_issues_found", count=len(issues))
    for issue in issues:
        if issue["state"] == "open":
            await close_issue(issue["number"], issue["title"])
        else:
            log.info("issue_already_closed", number=issue["number"], title=issue["title"])
        await asyncio.sleep(0.5)

    prs = await get_open_prs()
    log.info("open_prs_found", count=len(prs))
    for pr in prs:
        await close_pr_and_delete_branch(pr)
        await asyncio.sleep(0.5)

    clear_session_store()

    log.info(
        "demo_reset_complete",
        issues_processed=len(issues),
        prs_closed=len(prs),
    )


if __name__ == "__main__":
    asyncio.run(main())
