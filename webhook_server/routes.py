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

# In-flight lock: issue numbers currently being processed.
# Prevents a race between the store.get() check and store.save() across
# concurrent webhook calls for the same issue.
_in_flight: set[int] = set()


def _verify_signature(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def _fetch_github_issue(issue_number: int) -> GitHubIssue:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://api.github.com/repos/{FORK_REPO}/issues/{issue_number}",
            headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        r.raise_for_status()
        data = r.json()
        return GitHubIssue(
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            html_url=data["html_url"],
        )


async def _trigger_devin(issue_number: int, issue_url: str) -> None:
    # Layer 1: in-flight lock (guards the gap between store check and store save)
    if issue_number in _in_flight:
        log.info("session_in_flight", issue_number=issue_number)
        return
    # Layer 2: persistent store check (survives restarts)
    if store.get(issue_number):
        log.info("session_already_exists", issue_number=issue_number)
        return

    _in_flight.add(issue_number)
    try:
        issue = await _fetch_github_issue(issue_number)
        # Layer 3: Devin idempotent=True handles any duplicates that slip through
        session = await devin_client.create_session(issue)
        store.save(issue_number, issue_url, session.session_id, session.url)
        log.info(
            "devin_triggered",
            issue_number=issue_number,
            session_id=session.session_id,
            session_url=session.url,
        )
    except Exception as e:
        log.error("devin_trigger_failed", issue_number=issue_number, error=str(e))
    finally:
        _in_flight.discard(issue_number)


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

    triggered = 0
    for alert in payload.alerts:
        if alert.status != "firing":
            continue
        issue_number_str = alert.labels.get("issue_number", "")
        issue_url = alert.labels.get("issue_url", "")
        if not issue_number_str:
            log.warning("webhook_missing_issue_number", labels=alert.labels)
            continue
        background_tasks.add_task(_trigger_devin, int(issue_number_str), issue_url)
        triggered += 1

    return {"status": "accepted", "sessions_triggered": triggered}


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
                store.update_status(rec.session_id, status.status_enum, pr_url)
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
