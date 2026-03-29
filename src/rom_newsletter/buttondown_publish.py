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

from rom_newsletter.config import get_buttondown_api_key, load_env

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


def load_html_and_subject(html_path: Path, json_path: Path) -> tuple[str, str]:
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
        subject = "Weekly — simulation AI & digital twins"
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
        "--api-version",
        default=None,
        help="Optional X-API-Version header (e.g. 2026-04-01)",
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


if __name__ == "__main__":
    main()
