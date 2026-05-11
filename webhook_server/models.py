from typing import Optional
from pydantic import BaseModel


class GrafanaAlert(BaseModel):
    status: str = ""
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: str = ""
    endsAt: str = ""


class GrafanaWebhookPayload(BaseModel):
    receiver: str = ""
    status: str = ""
    alerts: list[GrafanaAlert] = []
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str
    html_url: str


class DevinSession(BaseModel):
    session_id: str
    url: str


class DevinSessionStatus(BaseModel):
    session_id: str
    status: str
    pull_requests: list[dict] = []
    updated_at: int = 0
