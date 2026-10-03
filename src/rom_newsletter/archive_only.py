"""Publish one existing reviewed draft to the web archive without subscriber delivery."""

from __future__ import annotations

import json
import argparse
import os
from pathlib import Path

import httpx

BUTTONDOWN_EMAILS_URL = "https://api.buttondown.com/v1/emails"


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
    if email.get("status") == "imported" and email.get("archival_mode") == "archive_only":
        return email
    if email.get("status") not in {"draft", "imported"}:
        raise ValueError("Archive-only publishing requires a draft or imported email; sent/queued issues need inspection")
    # /publish explicitly queues delivery. Import content directly into the
    # archive instead, without passing through any sending state.
    try:
        response = client.patch(url, json={"status": "imported", "archival_mode": "archive_only"})
        response.raise_for_status()
    except (httpx.TransportError, httpx.HTTPStatusError):
        # A write may have been accepted despite an error. Read once; never
        # repeat a write or fall back to /publish.
        pass
    email = get_email()
    if email.get("status") != "imported" or email.get("archival_mode") != "archive_only":
        raise RuntimeError("Archive import outcome is unconfirmed; inspect Buttondown before retrying")
    return email


def main():
    parser = argparse.ArgumentParser(description="Import a reviewed draft into the archive without queuing delivery")
    parser.add_argument("--email-id", required=True)
    parser.add_argument("--issue-key", required=True)
    args = parser.parse_args()
    with httpx.Client(
        headers={"Authorization": f"Token {os.environ['BUTTONDOWN_API_KEY']}"},
        timeout=120,
    ) as client:
        email = publish_archive_only(client, args.email_id, args.issue_key)
    result = {key: email.get(key) for key in ("id", "status", "archival_mode", "email_type", "absolute_url")}
    Path("archive-only-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Issue status: {result['status']}; archive mode: {result['archival_mode']}\n\nDelivery must be checked separately.\n")


if __name__ == "__main__":
    main()
