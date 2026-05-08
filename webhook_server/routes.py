import asyncio
import hashlib
import hmac
import json
import os

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

import devin_client
from models import GitHubIssue, GrafanaWebhookPayload
from session_store import SessionStore

log = structlog.get_logger()

router = APIRouter()
store = SessionStore()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
FORK_REPO = os.getenv("GITHUB_FORK_REPO", "")
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
TERMINAL_STATUSES = {"finished", "blocked", "expired"}

_github_http = httpx.AsyncClient(
    timeout=30,
    headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    },
)

# In-flight lock: issue numbers currently being processed.
# Prevents a race between the store.get() check and store.save() across
# concurrent webhook calls for the same issue.
_in_flight: set[int] = set()


def _verify_signature(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def _fetch_open_fork_issues() -> list[GitHubIssue]:
    """Fetch all open issues from the fork repo, excluding PRs."""
    r = await _github_http.get(
        f"https://api.github.com/repos/{FORK_REPO}/issues",
        params={"state": "open", "per_page": 100},
    )
    r.raise_for_status()
    return [
        GitHubIssue(
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            html_url=item["html_url"],
        )
        for item in r.json()
        if "pull_request" not in item
    ]


async def _trigger_devin(issue: GitHubIssue) -> None:
    # Layer 1: in-flight lock (guards the gap between store check and store save)
    if issue.number in _in_flight:
        log.info("session_in_flight", issue_number=issue.number)
        return
    # Layer 2: persistent store check (survives restarts)
    if store.get(issue.number):
        log.info("session_already_exists", issue_number=issue.number)
        return

    _in_flight.add(issue.number)
    try:
        # Layer 3: Devin idempotent=True handles any duplicates that slip through
        session = await devin_client.create_session(issue)
        await store.save(issue.number, issue.html_url, session.session_id, session.url)
        log.info(
            "devin_triggered",
            issue_number=issue.number,
            session_id=session.session_id,
            session_url=session.url,
        )
    except Exception as e:
        log.error("devin_trigger_failed", issue_number=issue.number, error=str(e))
    finally:
        _in_flight.discard(issue.number)


async def _handle_pending_issues() -> None:
    """Fetch all open fork issues and trigger Devin for any without an active session."""
    try:
        open_issues = await _fetch_open_fork_issues()
    except Exception as e:
        log.error("fetch_open_issues_failed", error=str(e))
        return

    log.info("pending_issues_check", open_count=len(open_issues))
    for issue in open_issues:
        if issue.number not in _in_flight and not store.get(issue.number):
            asyncio.create_task(_trigger_devin(issue))


@router.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig = request.headers.get("X-Grafana-Signature", "")
    if WEBHOOK_SECRET and not _verify_signature(body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = GrafanaWebhookPayload.model_validate(json.loads(body))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    any_firing = any(a.status == "firing" for a in payload.alerts)
    if not any_firing:
        return {"status": "no_firing_alerts"}

    background_tasks.add_task(_handle_pending_issues)
    return {"status": "accepted"}


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/sessions")
async def list_sessions():
    return [r.model_dump(mode="json") for r in store.get_all()]


async def poll_sessions() -> None:
    """Background task: refresh Devin session statuses every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        for rec in store.get_all():
            if rec.status in TERMINAL_STATUSES:
                continue
            try:
                status = await devin_client.get_session(rec.session_id)
                pr_url = (
                    status.pull_request.get("url") if status.pull_request else None
                )
                await store.update_status(rec.session_id, status.status_enum, pr_url)
                log.info(
                    "session_status_updated",
                    session_id=rec.session_id,
                    issue_number=rec.issue_number,
                    status=status.status_enum,
                    pr_url=pr_url,
                )
            except Exception as e:
                log.warning(
                    "session_poll_failed", session_id=rec.session_id, error=str(e)
                )
