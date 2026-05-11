#!/usr/bin/env python3
"""
Polls ngrok for the HTTPS tunnel URL, then upserts the Grafana webhook contact point.
Runs once at compose startup via the grafana_updater service, then exits.
"""
import os
import sys
import time

import httpx

NGROK_API = "http://ngrok:4040/api/tunnels"
GRAFANA_URL = os.environ["GRAFANA_URL"].rstrip("/")
GRAFANA_SA_TOKEN = os.environ["GRAFANA_SA_TOKEN"]
CONTACT_POINT_NAME = "devin-autoremediation"


def get_ngrok_url(retries: int = 30, delay: float = 2.0) -> str:
    for attempt in range(retries):
        try:
            r = httpx.get(NGROK_API, timeout=5)
            for tunnel in r.json().get("tunnels", []):
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            pass
        print(f"Waiting for ngrok tunnel... ({attempt + 1}/{retries})", flush=True)
        time.sleep(delay)
    raise SystemExit("ngrok tunnel unavailable after retries — check NGROK_AUTHTOKEN")


def upsert_contact_point(webhook_url: str) -> None:
    headers = {
        "Authorization": f"Bearer {GRAFANA_SA_TOKEN}",
        "Content-Type": "application/json",
        "X-Disable-Provenance": "true",
    }

    r = httpx.get(
        f"{GRAFANA_URL}/api/v1/provisioning/contact-points",
        headers=headers,
        timeout=10,
    )
    r.raise_for_status()
    existing = {cp["name"]: cp for cp in r.json()}

    payload = {
        "name": CONTACT_POINT_NAME,
        "type": "webhook",
        "settings": {
            "url": f"{webhook_url}/webhook",
            "httpMethod": "POST",
        },
    }

    if CONTACT_POINT_NAME in existing:
        uid = existing[CONTACT_POINT_NAME]["uid"]
        r = httpx.put(
            f"{GRAFANA_URL}/api/v1/provisioning/contact-points/{uid}",
            headers=headers,
            json={**payload, "uid": uid},
            timeout=10,
        )
    else:
        r = httpx.post(
            f"{GRAFANA_URL}/api/v1/provisioning/contact-points",
            headers=headers,
            json=payload,
            timeout=10,
        )
    r.raise_for_status()
    print(f"Contact point '{CONTACT_POINT_NAME}' → {webhook_url}/webhook", flush=True)


if __name__ == "__main__":
    url = get_ngrok_url()
    print(f"ngrok URL: {url}", flush=True)
    upsert_contact_point(url)
    print("Done.", flush=True)
