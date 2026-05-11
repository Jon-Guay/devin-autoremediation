import os

import httpx
import structlog

from models import DevinSession, DevinSessionStatus, GitHubIssue

log = structlog.get_logger()

DEVIN_API_KEY = os.environ["DEVIN_API_KEY"]
ORG_ID = os.environ["DEVIN_ORG_ID"]
FORK_REPO = os.getenv("GITHUB_FORK_REPO", "")
BASE_URL = "https://api.devin.ai/v3"

_http = httpx.AsyncClient(
    timeout=30,
    headers={
        "Authorization": f"Bearer {DEVIN_API_KEY}",
        "Content-Type": "application/json",
    },
)


def build_prompt(issue: GitHubIssue) -> str:
    return f"""You are an expert software engineer working on: https://github.com/{FORK_REPO}

Your task is to resolve the following GitHub issue end-to-end — investigation, fix, PR, and cleanup.

---

**Issue #{issue.number}: {issue.title}**

{issue.body}

---
### YOUR INSTRUCTIONS (follow these exactly, in order)

1. **Understand the issue** — read the code, reproduce the problem if possible, identify the root cause before touching anything.

2. **Fix it** — make a minimal, targeted fix. Do not refactor unrelated code or expand scope.

3. **Verify** — run existing tests to confirm nothing is broken. If there are no tests for this area, add one.

4. **Open a pull request** with:
   - A clear title and description explaining what you changed and why
   - The line `Closes #{issue.number}` in the PR body so GitHub auto-links the fix

5. **Close this issue** — after the PR is opened, close issue #{issue.number} on https://github.com/{FORK_REPO} directly. Do not wait for the PR to be merged.

Begin now."""


async def create_session(issue: GitHubIssue) -> DevinSession:
    r = await _http.post(
        f"{BASE_URL}/organizations/{ORG_ID}/sessions",
        json={
            "prompt": build_prompt(issue),
            "repos": [f"https://github.com/{FORK_REPO}"],
        },
    )
    r.raise_for_status()
    data = r.json()
    session = DevinSession(
        session_id=data["session_id"],
        url=data.get("url", f"https://app.devin.ai/sessions/{data['session_id']}"),
    )
    log.info(
        "devin_session_created",
        session_id=session.session_id,
        issue_number=issue.number,
    )
    return session


async def get_session(session_id: str) -> DevinSessionStatus:
    r = await _http.get(f"{BASE_URL}/organizations/{ORG_ID}/sessions/{session_id}")
    r.raise_for_status()
    data = r.json()
    return DevinSessionStatus(
        session_id=session_id,
        status=data.get("status", "unknown"),
        pull_requests=data.get("pull_requests", []),
        updated_at=data.get("updated_at", 0),
    )
