import os

import httpx
import structlog

from models import DevinSession, DevinSessionStatus, GitHubIssue

log = structlog.get_logger()

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]
FORK_REPO = os.getenv("GITHUB_FORK_REPO", "")
BASE_URL = "https://api.devin.ai/v1"

_http = httpx.AsyncClient(
    timeout=30,
    headers={
        "Authorization": f"Bearer {DEVIN_API_KEY}",
        "Content-Type": "application/json",
    },
)


def build_prompt(issue: GitHubIssue) -> str:
    return f"""You are working on the GitHub repository: https://github.com/{FORK_REPO}

Please fix the following issue:

**Issue #{issue.number}: {issue.title}**

{issue.body}

---

Instructions:
1. Investigate the issue thoroughly before making changes
2. Create a minimal, focused fix — do not change unrelated code
3. Ensure existing tests still pass after your changes
4. Open a pull request with a clear description referencing this issue

Please begin your investigation now."""


async def create_session(issue: GitHubIssue) -> DevinSession:
    r = await _http.post(
        f"{BASE_URL}/sessions",
        json={"prompt": build_prompt(issue), "idempotent": True},
    )
    r.raise_for_status()
    data = r.json()
    session = DevinSession(
        session_id=data["session_id"],
        url=data.get("url", f"https://app.devin.ai/sessions/{data['session_id']}"),
        is_new_session=data.get("is_new_session", True),
    )
    log.info(
        "devin_session_created",
        session_id=session.session_id,
        issue_number=issue.number,
        is_new=session.is_new_session,
    )
    return session


async def get_session(session_id: str) -> DevinSessionStatus:
    r = await _http.get(f"{BASE_URL}/sessions/{session_id}")
    r.raise_for_status()
    data = r.json()
    return DevinSessionStatus(
        session_id=session_id,
        status_enum=data.get("status_enum", "unknown"),
        pull_request=data.get("pull_request"),
        updated_at=data.get("updated_at", ""),
    )
