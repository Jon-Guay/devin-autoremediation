import asyncio
import os
from datetime import datetime, timezone

import structlog

import github_client
from models import MirroredIssue, ScoredIssue

log = structlog.get_logger()

FORK_REPO = os.environ["GITHUB_FORK_REPO"]
SAFE_LABELS = {"bug", "enhancement", "documentation"}


async def mirror_issues(issues: list[ScoredIssue]) -> list[MirroredIssue]:
    mirrored: list[MirroredIssue] = []

    for issue in issues:
        existing = await github_client.search_issues_in_repo(
            FORK_REPO, f'in:body "Source: {issue.html_url}"'
        )
        if existing:
            log.info(
                "issue_already_mirrored",
                upstream_number=issue.number,
                fork_number=existing[0]["number"],
            )
            continue

        body = (
            f"{issue.body}\n\n"
            f"---\n"
            f"**Source:** {issue.html_url}\n"
            f"**AI Score:** {issue.ai_score}/10\n"
            f"**Reasoning:** {issue.ai_reasoning}\n"
        )
        labels = [lb for lb in issue.labels if lb in SAFE_LABELS]

        created = await github_client.create_issue(
            repo=FORK_REPO,
            title=issue.title,
            body=body,
            labels=labels,
        )

        record = MirroredIssue(
            fork_issue_number=created["number"],
            upstream_issue_number=issue.number,
            fork_issue_url=created["html_url"],
            upstream_url=issue.html_url,
            created_at=datetime.now(timezone.utc),
        )
        mirrored.append(record)

        log.info(
            "issue_mirrored",
            fork_number=record.fork_issue_number,
            upstream_number=record.upstream_issue_number,
            ai_score=issue.ai_score,
            fork_url=record.fork_issue_url,
        )

        await asyncio.sleep(1)

    log.info("mirror_complete", total_mirrored=len(mirrored))
    return mirrored
