"""Publish a validated issue, recovering the same Buttondown email across reruns."""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from filelock import FileLock

from rom_newsletter.compose import NewsletterDraft, validate_citations
from rom_newsletter.config import get_buttondown_api_key, load_env, project_root
from rom_newsletter.history import merge_history, read_json, write_json_atomic
from rom_newsletter.search import SearchHit

BUTTONDOWN_EMAILS_URL = "https://api.buttondown.com/v1/emails"
FANCY_PREFIX = "<!-- buttondown-editor-mode: fancy -->\n"
_ACCEPTED = {"about_to_send", "in_flight", "sent", "scheduled", "throttled", "resending", "imported"}
_RETRYABLE = {429, 502, 503, 504}


def newsletter_paths(output_dir: Path, stamp: str) -> tuple[Path, Path]:
    base = output_dir / f"newsletter-{stamp}"
    return base.with_suffix(".html"), base.with_suffix(".json")


def load_html_and_subject(html_path: Path, json_path: Path, *, topic_path: Path | None = None) -> tuple[str, str]:
    html = html_path.read_text(encoding="utf-8")
    data = read_json(json_path)
    subject = data.get("subject", "").strip()
    if not subject:
        from rom_newsletter.topic import load_topic, load_topic_for_run

        topic = load_topic(topic_path) if topic_path else load_topic_for_run(project_root(), None)
        subject = topic.buttondown_fallback_subject
    return html, subject


def build_buttondown_body(html: str) -> str:
    return FANCY_PREFIX + html


def _get(client: httpx.Client, url: str) -> dict:
    # Do not forward credentials to an unexpected pagination host.
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "api.buttondown.com" or not parsed.path.startswith("/v1/emails"):
        raise ValueError("Unexpected Buttondown API URL")
    for attempt in range(4):
        try:
            response = client.get(url)
        except httpx.TransportError:
            if attempt == 3:
                raise
        else:
            if response.status_code not in _RETRYABLE or attempt == 3:
                response.raise_for_status()
                return response.json()
        time.sleep(2**attempt)
    raise RuntimeError("Buttondown lookup failed")


def _find_email(client: httpx.Client, issue_key: str, legacy_marker: str | None = None) -> dict | None:
    match = None
    page_url: str | None = BUTTONDOWN_EMAILS_URL
    visited: set[str] = set()
    while page_url:
        if page_url in visited:
            raise RuntimeError("Buttondown returned a pagination cycle")
        visited.add(page_url)
        data = _get(client, page_url)
        for email in data.get("results", []):
            metadata = email.get("metadata") or {}
            body = email.get("body") or ""
            tagged = metadata.get("rom_newsletter_issue") == issue_key
            # Legacy fallback only for emails without the new issue metadata.
            legacy = not metadata.get("rom_newsletter_issue") and legacy_marker and legacy_marker in body
            if tagged or legacy:
                if match is not None and match["id"] != email["id"]:
                    raise RuntimeError(f"Multiple Buttondown emails match {issue_key}; resolve them before publishing")
                match = email
        page_url = data.get("next") or None
    return match


def publish_issue(
    *,
    html: str,
    subject: str,
    issue_key: str,
    week_end: str,
    token: str,
    state_path: Path,
    draft: bool = False,
    api_version: str | None = None,
    transport: httpx.BaseTransport | None = None,
    selected_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Create a draft once, save its ID, then queue it once.

    An ambiguous create or send is reconciled by GET. If acceptance cannot be
    confirmed, stop with pending state instead of repeating a potentially accepted write.
    Caller holds the state-file lock for the entire transaction.
    """
    state = read_json(state_path)
    receipts = state.setdefault("issues", {})
    receipt = receipts.get(issue_key, {})
    headers = {"Authorization": f"Token {token}", "X-Buttondown-Live-Dangerously": "true"}
    if api_version:
        headers["X-API-Version"] = api_version
    body = build_buttondown_body(html)
    with httpx.Client(headers=headers, timeout=120.0, transport=transport) as client:
        if receipt.get("email_id"):
            # A saved identity is authoritative. A deleted email must not cause a new send.
            email = _get(client, f"{BUTTONDOWN_EMAILS_URL}/{receipt['email_id']}")
        else:
            email = _find_email(
                client, issue_key, f"Week of {week_end}" if issue_key.startswith("rom-sciml-twins:") else None
            )
        if email is None:
            if receipt.get("create_pending"):
                raise RuntimeError(
                    "Previous create has an unknown outcome; no matching email found. Inspect Buttondown and publication state before retrying."
                )
            receipt = {"create_pending": True}
            receipts[issue_key] = receipt
            write_json_atomic(state_path, state)
            try:
                response = client.post(
                    BUTTONDOWN_EMAILS_URL,
                    json={
                        "subject": subject,
                        "body": body,
                        "status": "draft",
                        "metadata": {"rom_newsletter_issue": issue_key, "rom_newsletter_sources": selected_urls or []},
                    },
                )
                response.raise_for_status()
                email = response.json()
                if not email.get("id"):
                    raise ValueError("Buttondown create response has no email ID")
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError):
                email = _find_email(client, issue_key)
                if email is None:
                    raise RuntimeError(
                        "Buttondown create outcome is unconfirmed; stopped without retrying POST. Pending state was saved."
                    ) from None
        receipt.setdefault("selected_urls", (email.get("metadata") or {}).get("rom_newsletter_sources", []))
        receipt.update({"email_id": email["id"], "create_pending": False, "status": email.get("status")})
        receipts[issue_key] = receipt
        write_json_atomic(state_path, state)  # Persist identity before any send request.
        status = email.get("status")
        if status == "draft":
            if receipt.get("send_pending"):
                raise RuntimeError(
                    "Previous send outcome is unconfirmed and email still appears draft; stopped without repeating send. Inspect Buttondown before clearing send_pending."
                )
            response = client.patch(
                f"{BUTTONDOWN_EMAILS_URL}/{email['id']}",
                json={
                    "subject": subject,
                    "body": body,
                    "metadata": {
                        **(email.get("metadata") or {}),
                        "rom_newsletter_issue": issue_key,
                        "rom_newsletter_sources": selected_urls or [],
                    },
                },
            )
            response.raise_for_status()
            email = response.json()
            receipt["selected_urls"] = selected_urls or []
            write_json_atomic(state_path, state)
            if not draft and email.get("status") == "draft":
                receipt["send_pending"] = True
                write_json_atomic(state_path, state)
                try:
                    response = client.patch(f"{BUTTONDOWN_EMAILS_URL}/{email['id']}", json={"status": "about_to_send"})
                    response.raise_for_status()
                    email = response.json()
                except (httpx.TransportError, httpx.HTTPStatusError, ValueError):
                    email = _get(client, f"{BUTTONDOWN_EMAILS_URL}/{receipt['email_id']}")
                if email.get("status") not in _ACCEPTED:
                    raise RuntimeError(
                        "Buttondown send outcome is unconfirmed; pending state saved, no send retry attempted"
                    )
        elif status == "sent":
            # Updating an already-sent email edits the archive without changing status.
            response = client.patch(
                f"{BUTTONDOWN_EMAILS_URL}/{email['id']}",
                json={
                    "subject": subject,
                    "body": body,
                    "metadata": {
                        **(email.get("metadata") or {}),
                        "rom_newsletter_issue": issue_key,
                        "rom_newsletter_sources": selected_urls or [],
                    },
                },
            )
            response.raise_for_status()
            email = response.json()
            receipt["selected_urls"] = selected_urls or []
        elif status not in _ACCEPTED:
            raise RuntimeError(f"Buttondown email status {status!r} requires inspection; will not resend")
        if not draft and email.get("status") not in _ACCEPTED:
            raise RuntimeError(f"Buttondown did not accept publication: {email.get('status')!r}")
        receipt.update({"status": email.get("status"), "send_pending": False})
        write_json_atomic(state_path, state)
        # Tell the caller which content was accepted. Queued emails are left untouched.
        email["content_updated"] = status in {"draft", "sent"}
        return email


def validate_issue_files(output_dir: Path, stamp: str) -> tuple[str, str, dict]:
    html_path, json_path = newsletter_paths(output_dir, stamp)
    html, subject = load_html_and_subject(html_path, json_path)
    manifest = read_json(output_dir / f"newsletter-{stamp}-selection.json")
    if manifest.get("week_end") != stamp:
        raise ValueError("Selection manifest has the wrong week")
    for name, path in (("html_sha256", html_path), ("json_sha256", json_path)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.get(name):
            raise ValueError(f"{path.name} no longer matches its validated selection manifest; regenerate the issue")
    audit = read_json(output_dir / f"newsletter-{stamp}-search.json")
    hits = [SearchHit(**row) for row in audit["composition_inputs"]]
    selected = validate_citations(NewsletterDraft.model_validate(read_json(json_path)), hits)
    if selected != manifest.get("selected_urls"):
        raise ValueError("Selection manifest disagrees with newsletter citations")
    return html, subject, manifest


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Publish a validated issue to Buttondown; reuse its email ID on reruns.")
    p.add_argument("--output-dir", type=Path, default=Path("dist"))
    p.add_argument("--date", dest="week_date", required=True)
    p.add_argument("--draft", action="store_true", help="Create/update a draft; do not queue it")
    p.add_argument("--dry-run", action="store_true", help="Validate files without calling Buttondown")
    p.add_argument("--state-file", type=Path, default=project_root() / ".rom-newsletter" / "publications.json")
    p.add_argument("--history-file", type=Path, default=None, help="Override the manifest's published-history path")
    p.add_argument("--api-version", default=None)
    args = p.parse_args(argv)
    stamp = date.fromisoformat(args.week_date).isoformat()
    html, subject, manifest = validate_issue_files(args.output_dir, stamp)
    if args.dry_run:
        print(
            f"Validated {manifest['issue_key']}: {len(manifest['selected_urls'])} cited sources; {len(html)} HTML characters"
        )
        return
    if not manifest["selected_urls"] and not args.draft:
        raise ValueError("No cited stories; refusing to send an empty issue")
    load_env()
    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.state_file) + ".lock", timeout=1):
        result = publish_issue(
            html=html,
            subject=subject,
            issue_key=manifest["issue_key"],
            week_end=stamp,
            token=get_buttondown_api_key(),
            state_path=args.state_file,
            draft=args.draft,
            api_version=args.api_version or os.environ.get("BUTTONDOWN_API_VERSION") or None,
            selected_urls=manifest["selected_urls"],
        )
        if result.get("status") in _ACCEPTED:
            # Only record citations from the content Buttondown actually accepted.
            history_path = args.history_file or Path(manifest["history_file"])
            if result["content_updated"]:
                urls = manifest["selected_urls"]
            else:
                state = read_json(args.state_file)
                urls = state["issues"][manifest["issue_key"]].get("selected_urls", [])
            merge_history(history_path, urls, issue_key=manifest["issue_key"])
        state = read_json(args.state_file)
        if result["content_updated"]:
            state["issues"][manifest["issue_key"]]["selected_urls"] = manifest["selected_urls"]
        write_json_atomic(args.state_file, state)
    print(f"Buttondown email {result['id']} status={result.get('status')}; publication state saved")


if __name__ == "__main__":
    main()
