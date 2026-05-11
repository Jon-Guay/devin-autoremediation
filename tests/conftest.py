"""Ensure webhook_server sees test env before any imports."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="webhook-test-")

os.environ["GITHUB_TOKEN"] = "test-github-token"
os.environ["DEVIN_API_KEY"] = "test-devin-api-key"
os.environ["DEVIN_ORG_ID"] = "org-test"
os.environ["GITHUB_FORK_REPO"] = "test/test"
os.environ["WEBHOOK_SECRET"] = ""
os.environ["SESSION_STORE_PATH"] = str(Path(_tmp) / "sessions.json")
