import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog
from pydantic import BaseModel

log = structlog.get_logger()

SESSION_STORE_PATH = Path(os.getenv("SESSION_STORE_PATH", "/data/sessions.json"))


class SessionRecord(BaseModel):
    issue_number: int
    issue_url: str
    session_id: str
    session_url: str
    status: str = "created"
    pr_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[int, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        if SESSION_STORE_PATH.exists():
            try:
                data = json.loads(SESSION_STORE_PATH.read_text())
                if not isinstance(data, list):
                    raise ValueError("sessions.json root must be a list")
                for item in data:
                    rec = SessionRecord.model_validate(item)
                    self._sessions[rec.issue_number] = rec
                log.info("session_store_loaded", count=len(self._sessions))
            except Exception as e:
                log.warning("session_store_load_failed", error=str(e))

    def _write(self) -> None:
        SESSION_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSION_STORE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                [r.model_dump(mode="json") for r in self._sessions.values()], indent=2
            )
        )
        tmp.rename(SESSION_STORE_PATH)

    async def _persist(self) -> None:
        await asyncio.to_thread(self._write)

    async def save(
        self, issue_number: int, issue_url: str, session_id: str, session_url: str
    ) -> SessionRecord:
        now = datetime.now(timezone.utc)
        rec = SessionRecord(
            issue_number=issue_number,
            issue_url=issue_url,
            session_id=session_id,
            session_url=session_url,
            created_at=now,
            updated_at=now,
        )
        self._sessions[issue_number] = rec
        await self._persist()
        return rec

    def get(self, issue_number: int) -> Optional[SessionRecord]:
        return self._sessions.get(issue_number)

    def get_all(self) -> list[SessionRecord]:
        return list(self._sessions.values())

    async def update_status(
        self, session_id: str, status: str, pr_url: Optional[str] = None
    ) -> None:
        for rec in self._sessions.values():
            if rec.session_id == session_id:
                rec.status = status
                rec.updated_at = datetime.now(timezone.utc)
                if pr_url:
                    rec.pr_url = pr_url
                await self._persist()
                return
