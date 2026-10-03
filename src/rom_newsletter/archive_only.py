"""Publish one existing reviewed draft to the web archive without subscriber delivery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from rom_newsletter.buttondown_publish import BUTTONDOWN_EMAILS_URL


def publish_archive_only(client: httpx.Client, email_id: str, issue_key: str) -> dict:
    url = f"{BUTTONDOWN_EMAILS_URL}/{email_id}"

    def get_email():
        response = client.get(url)
        response.raise_for_status()
        email = response.json()
        if email.get("id") != email_id or (email.get("metadata") or {}).get("rom_newsletter_issue") != issue_key:
            raise ValueError("Reviewed issue identity does not match")
        return email

    email = get_email()
    if email.get("status") in {"sent", "imported"} and email.get("archival_mode") == "archive_only":
        return email
    if email.get("status") != "draft":
        raise ValueError("Archive-only publishing requires the reviewed draft")
    body = (email.get("body") or "").replace("DRAFT PREVIEW · NOT SENT", "ARCHIVE EDITION")
    body = body.replace("Draft for review; no email send is authorized.", "Published to the web archive only; no subscriber email delivery.")
    response = client.patch(url, json={"archival_mode": "archive_only", "body": body})
    response.raise_for_status()
    email = get_email()
    if email.get("archival_mode") != "archive_only" or email.get("status") != "draft":
        raise ValueError("Archive-only mode was not confirmed; refusing to publish")
    try:
        # Explicitly preserve archive-only mode in the publish transaction too.
        response = client.post(url + "/publish", json={"archival_mode": "archive_only"})
        response.raise_for_status()
    except (httpx.TransportError, httpx.HTTPStatusError):
        # Reconcile once; never blindly repeat a publish request.
        email = get_email()
        if email.get("archival_mode") != "archive_only" or email.get("status") not in {"sent", "imported"}:
            raise RuntimeError("Archive publication outcome is unconfirmed; inspect before retrying") from None
    email = get_email()
    if email.get("archival_mode") != "archive_only" or email.get("status") not in {"sent", "imported"}:
        raise RuntimeError("Buttondown has not confirmed archive publication")
    return email


def main():
    request = json.loads(Path("issues/2026-09-27/archive-only.json").read_text())
    with httpx.Client(
        headers={"Authorization": f"Token {os.environ['BUTTONDOWN_API_KEY']}"},
        timeout=120,
    ) as client:
        email = publish_archive_only(client, request["email_id"], request["issue_key"])
    result = {key: email.get(key) for key in ("id", "status", "archival_mode", "email_type", "absolute_url")}
    Path("archive-only-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Archive-only publication confirmed: {result['absolute_url']}\n\nNo subscriber email delivery.\n")


if __name__ == "__main__":
    main()
