"""Publish rendered newsletter HTML to Buttondown (https://buttondown.com/)."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from rom_newsletter.config import get_buttondown_api_key, load_env, project_root

BUTTONDOWN_EMAILS_URL = "https://api.buttondown.com/v1/emails"
FANCY_PREFIX = "<!-- buttondown-editor-mode: fancy -->\n"

# api.buttondown.com can return 503 (often Heroku "Application Error" HTML) during incidents; retry.
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_MAX_ATTEMPTS = 6
_RETRY_BASE_SEC = 2.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = (response.headers.get("Retry-After") or "").strip()
    if raw.isdigit():
        return float(raw)
    return None


def _post_emails_with_retries(
    client: httpx.Client,
    *,
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    for attempt in range(_MAX_ATTEMPTS):
        try:
            r = client.post(url, json=json_body, headers=headers)
        except httpx.TimeoutException:
            if attempt < _MAX_ATTEMPTS - 1:
                delay = _RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.5)
                time.sleep(delay)
                continue
            raise
        if r.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
            delay = _RETRY_BASE_SEC * (2**attempt) + random.uniform(0, 0.5)
            if r.status_code == 429:
                ra = _retry_after_seconds(r)
                if ra is not None:
                    delay = max(delay, ra)
            time.sleep(delay)
            continue
        r.raise_for_status()
        return r


def newsletter_paths(output_dir: Path, stamp: str) -> tuple[Path, Path]:
    """Paths to ``newsletter-{stamp}.html`` and ``.json``."""
    base = output_dir / f"newsletter-{stamp}"
    return base.with_suffix(".html"), base.with_suffix(".json")


def load_html_and_subject(
    html_path: Path,
    json_path: Path,
    *,
    topic_path: Path | None = None,
) -> tuple[str, str]:
    if not html_path.is_file():
        raise FileNotFoundError(f"Missing HTML: {html_path}")
    html = html_path.read_text(encoding="utf-8")
    subject = ""
    if json_path.is_file():
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            s = data.get("subject")
            if isinstance(s, str) and s.strip():
                subject = s.strip()
    if not subject:
        from rom_newsletter.topic import load_topic, load_topic_for_run

        if topic_path is not None and topic_path.is_file():
            subject = load_topic(topic_path).buttondown_fallback_subject
        else:
            subject = load_topic_for_run(project_root(), None).buttondown_fallback_subject
    return html, subject


def build_buttondown_body(html: str) -> str:
    """Prepend fancy HTML mode; avoids accidental YAML frontmatter at start of body."""
    return FANCY_PREFIX + html


def publish_to_buttondown(
    *,
    html: str,
    subject: str,
    token: str,
    draft: bool = False,
    api_version: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Create a Buttondown email. Sends to subscribers when *draft* is false (``about_to_send``).

    Uses ``X-Buttondown-Live-Dangerously: true`` so the first ``about_to_send`` succeeds under
    API version ``2026-04-01``+ and to allow edge-case bodies (see Buttondown docs).
    """
    body = build_buttondown_body(html)
    headers: dict[str, str] = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "X-Buttondown-Live-Dangerously": "true",
    }
    if api_version:
        headers["X-API-Version"] = api_version
    payload: dict[str, Any] = {
        "subject": subject[:2000],
        "body": body,
        "status": "draft" if draft else "about_to_send",
    }
    client_timeout = httpx.Timeout(connect=20.0, read=timeout, write=30.0, pool=60.0)
    with httpx.Client(timeout=client_timeout) as client:
        r = _post_emails_with_retries(
            client,
            url=BUTTONDOWN_EMAILS_URL,
            json_body=payload,
            headers=headers,
        )
        return r.json()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Upload dist/newsletter-<date>.html to Buttondown using the JSON subject line."
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Directory containing newsletter-<date>.html (default: dist)",
    )
    p.add_argument(
        "--date",
        dest="week_date",
        required=True,
        help="ISO date YYYY-MM-DD (same as rom-newsletter --date; matches newsletter-<date>.*)",
    )
    p.add_argument(
        "--draft",
        action="store_true",
        help="Create a draft only (status=draft); do not send to subscribers",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load files and print subject + body length; do not call the API",
    )
    p.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Create a new email even if an email for this week already exists (do not update in place)",
    )
    p.add_argument(
        "--api-version",
        default=None,
        help="Optional X-API-Version header (e.g. 2026-04-01)",
    )
    p.add_argument(
        "--topic",
        type=Path,
        default=None,
        help="Topic profile JSON (fallback subject when JSON has no subject; default: env or <repo>/topic.json)",
    )
    args = p.parse_args(argv)
    stamp = args.week_date.strip()
    html_path, json_path = newsletter_paths(args.output_dir, stamp)
    try:
        html, subject = load_html_and_subject(html_path, json_path)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    if args.dry_run:
        body = build_buttondown_body(html)
        print(f"Subject: {subject}")
        print(f"Body chars: {len(body)} (html file: {html_path})")
        return
    load_env()
    token = get_buttondown_api_key()
    api_ver = (args.api_version or os.environ.get("BUTTONDOWN_API_VERSION", "")).strip() or None
    # Regeneration behavior: our template renders "Week of <date>" at the top of the body.
    # If an email for this week already exists, update its subject+body in place (web
    # archive only, no new email, no re-send to subscribers). Otherwise create a new email.
    # Skipped when the marker is missing from this HTML (e.g. an old template) so we never
    # accidentally match an unrelated week's email.
    marker = f"Week of {stamp}"
    prior_id: str | None = None
    if not args.no_dedupe and marker in html:
        prior_id = _find_email_containing(token, marker)
    if prior_id is not None:
        try:
            out = _update_email(
                token,
                prior_id,
                subject=subject,
                body=build_buttondown_body(html),
                api_version=api_ver,
            )
        except httpx.HTTPStatusError as e:
            print(
                f"Buttondown API error updating existing email {prior_id}: {e}",
                file=sys.stderr,
            )
            if e.response is not None:
                print(e.response.text[:4000], file=sys.stderr)
            sys.exit(1)
        except httpx.TimeoutException as e:
            print(
                f"Buttondown API timed out updating email {prior_id}: {e}. Retry later.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"Updated existing Buttondown email {prior_id} "
            f"(week {stamp}); no new email sent."
        )
        print(f"Buttondown email {prior_id} status={out.get('status')}")
        return
    try:
        out = publish_to_buttondown(
            html=html,
            subject=subject,
            token=token,
            draft=args.draft,
            api_version=api_ver,
        )
    except httpx.HTTPStatusError as e:
        print(f"Buttondown API error: {e}", file=sys.stderr)
        if e.response is not None:
            text = e.response.text[:4000]
            if "<html" in text.lower() or "heroku" in text.lower():
                print(
                    "(Response was HTML—often a temporary 503 from Buttondown's host; "
                    f"retries already attempted ({_MAX_ATTEMPTS}). Re-run the workflow later.)",
                    file=sys.stderr,
                )
            else:
                print(text, file=sys.stderr)
        sys.exit(1)
    except httpx.TimeoutException as e:
        print(
            f"Buttondown API timed out after {_MAX_ATTEMPTS} attempts: {e}. Retry later.",
            file=sys.stderr,
        )
        sys.exit(1)
    eid = out.get("id", "?")
    st = out.get("status", "?")
    print(f"Buttondown email {eid} status={st}")


def _find_email_containing(token: str, marker: str) -> str | None:
    """Return the id of the existing Buttondown email whose body contains *marker*.

    ``marker`` is the week label (e.g. "Week of 2026-08-23") rendered at the top of the
    newsletter HTML body. If multiple emails match (should not happen), the most recent is
    returned. Returns ``None`` when no prior email exists for that week.
    """
    headers = {"Authorization": f"Token {token}"}
    match: dict | None = None
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        page_url: str | None = BUTTONDOWN_EMAILS_URL
        while page_url:
            r = client.get(page_url, headers=headers)
            r.raise_for_status()
            data = r.json()
            for email in data.get("results", []):
                if marker not in (email.get("body") or ""):
                    continue
                if match is None or (email.get("creation_date") or "") >= (
                    match.get("creation_date") or ""
                ):
                    match = email
            page_url = data.get("next") or None
    return match.get("id") if match else None


def _update_email(
    token: str,
    email_id: str,
    *,
    subject: str,
    body: str,
    api_version: str | None = None,
) -> dict:
    """Update an existing Buttondown email's subject and body (web archive) without resending.

    Updating a ``sent`` email only changes the archived copy; it does not push a new email
    to subscribers. Returns the updated email object.
    """
    headers: dict[str, str] = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }
    if api_version:
        headers["X-API-Version"] = api_version
    payload = {
        "subject": subject[:2000],
        "body": body,
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.patch(
            f"{BUTTONDOWN_EMAILS_URL}/{email_id}",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    main()
