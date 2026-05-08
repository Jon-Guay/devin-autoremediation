import asyncio
import os

import structlog
import structlog.stdlib
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

SOURCE_REPO = os.getenv("GITHUB_SOURCE_REPO", "apache/superset")
SCAN_LABELS = ["good first issue", "easy", "help wanted", "bug"]
SCAN_KEYWORDS = [
    "typo", "missing", "simple", "UI", "docs", "test",
    "fix", "broken", "incorrect", "wrong", "error",
]
MAX_CANDIDATES = 30
TOP_N = 5


async def main() -> None:
    log.info("scanner_start", source_repo=SOURCE_REPO)

    from github_client import get_candidate_issues
    from ai_filter import score_issues
    from issue_mirror import mirror_issues

    candidates = await get_candidate_issues(
        repo=SOURCE_REPO,
        labels=SCAN_LABELS,
        keywords=SCAN_KEYWORDS,
        max_results=MAX_CANDIDATES,
    )

    if not candidates:
        log.warning("no_candidates_found")
        return

    top_issues = await score_issues(candidates, top_n=TOP_N)

    if not top_issues:
        log.warning("no_issues_passed_ai_filter")
        return

    mirrored = await mirror_issues(top_issues)
    log.info("scanner_complete", mirrored_count=len(mirrored))


if __name__ == "__main__":
    asyncio.run(main())
