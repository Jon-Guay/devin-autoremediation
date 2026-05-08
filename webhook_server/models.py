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
    is_new_session: bool = True


class DevinSessionStatus(BaseModel):
    session_id: str
    status_enum: str
    pull_request: Optional[dict] = None
    updated_at: str = ""
