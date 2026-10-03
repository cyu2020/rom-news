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
    raise RuntimeError("Archive-only publishing is disabled: the publish endpoint caused subscriber delivery")


def main():
    request = json.loads(Path("issues/2026-09-27/archive-only.json").read_text())
    with httpx.Client(
        headers={"Authorization": f"Token {os.environ['BUTTONDOWN_API_KEY']}"},
        timeout=120,
    ) as client:
        if request.get("verify_only"):
            response = client.get(f"{BUTTONDOWN_EMAILS_URL}/{request['email_id']}")
            response.raise_for_status()
            email = response.json()
        else:
            email = publish_archive_only(client, request["email_id"], request["issue_key"])
    result = {key: email.get(key) for key in ("id", "status", "archival_mode", "email_type", "absolute_url")}
    Path("archive-only-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Issue status: {result['status']}; archive mode: {result['archival_mode']}\n\nDelivery must be checked separately.\n")


if __name__ == "__main__":
    main()
