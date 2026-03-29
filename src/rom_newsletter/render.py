from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from rom_newsletter.compose import MajorSection, NewsletterDraft, Subsection


def _split_paras(text: str) -> list[str]:
    chunks = [p.strip() for p in text.split("\n\n")]
    out = [c for c in chunks if c]
    return out if out else [text.strip() or ""]


def _escape_para(p: str) -> str:
    safe = html.escape(p, quote=False)
    return safe.replace("\n", "<br>\n")


def _subsection_view(sub: Subsection) -> dict[str, Any]:
    return {
        "title": sub.title,
        "paras": [_escape_para(p) for p in _split_paras(sub.body)],
        "links": [{"url": lr.url, "label": lr.label} for lr in (sub.links or [])],
    }


def _major_view(ms: MajorSection) -> dict[str, Any]:
    return {
        "intro_paras": [_escape_para(p) for p in _split_paras(ms.intro)],
        "subsections": [_subsection_view(s) for s in ms.subsections],
    }


def render_html(
    draft: NewsletterDraft,
    *,
    week_label: str,
    template_dir: Path | None = None,
) -> str:
    base = template_dir or (Path(__file__).resolve().parent.parent.parent / "templates")
    env = Environment(
        loader=FileSystemLoader(str(base)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    tpl = env.get_template("newsletter.html.j2")
    return tpl.render(
        subject=draft.subject,
        week_label=week_label,
        research=_major_view(draft.research_papers),
        industry=_major_view(draft.industry_news),
    )
