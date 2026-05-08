from datetime import datetime
from pydantic import BaseModel


class RawIssue(BaseModel):
    number: int
    title: str
    body: str
    labels: list[str]
    html_url: str
    created_at: datetime


class ScoredIssue(RawIssue):
    ai_score: float
    ai_reasoning: str


class MirroredIssue(BaseModel):
    fork_issue_number: int
    upstream_issue_number: int
    fork_issue_url: str
    upstream_url: str
    created_at: datetime
