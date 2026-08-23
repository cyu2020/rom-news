from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

from rom_newsletter.config import llm_api_key, llm_base_url
from rom_newsletter.topic import TopicProfile


class LinkRef(BaseModel):
    url: str
    label: str | None = None


class Subsection(BaseModel):
    title: str
    body: str
    links: list[LinkRef] = Field(default_factory=list)


class MajorSection(BaseModel):
    """One top-level block (e.g. Research Papers) with its own intro and subsections."""

    intro: str
    subsections: list[Subsection] = Field(min_length=1, max_length=5)


class NewsletterDraft(BaseModel):
    subject: str
    industry_news: MajorSection
    research_papers: MajorSection


_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_MAX_REPAIR_ATTEMPTS = 3

_COMPOSE_HARD_RULES = """Hard rules:
- Use ONLY the facts implied by the provided search excerpts. Do not invent venues, dates, product names, or paper titles that are not supported by the excerpts.
- Every substantive claim must be traceable to at least one provided URL. Prefer citing by paraphrasing the excerpt, not by guessing details.
- If excerpts are thin for one track, write shorter subsections there rather than speculating."""

_COMPOSE_JSON_STRUCTURE = """Structure — output a single JSON object (no markdown, no prose outside JSON) with exactly this shape (industry_news is listed first to reflect editorial priority):
{
  "subject": "email subject line, under 90 characters",
  "industry_news": {
    "intro": "short intro for the Industry News block",
    "subsections": [
      {
        "title": "subsection heading",
        "body": "1-2 short paragraphs; separate paragraphs with a blank line",
        "links": [{"url": "https://...", "label": "optional short label"}]
      }
    ]
  },
  "research_papers": {
    "intro": "short intro for the Research Papers block; paragraphs separated by a blank line if needed",
    "subsections": [ same shape as industry_news subsections ]
  }
}"""


def _compose_system_prompt(topic: TopicProfile) -> str:
    return (
        f"{topic.compose.editor_intro}\n\n"
        f"{_COMPOSE_HARD_RULES}\n\n"
        f"{topic.compose.subject_rules}\n\n"
        f"{_COMPOSE_JSON_STRUCTURE}\n\n"
        f"{topic.compose.classification_block}"
    )


def _strip_json_payload(text: str) -> str:
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        return m.group(1).strip()
    return text


def _parse_newsletter_json(text: str) -> NewsletterDraft:
    payload = _strip_json_payload(text)
    data = json.loads(payload)
    return NewsletterDraft.model_validate(data)


def _heal_json_llm(
    client: OpenAI,
    model: str,
    broken: str,
    *,
    previous_error: str = "",
) -> str:
    fix = client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": (
                    "Reply with a single valid JSON object only. No markdown fence, no commentary. "
                    "Schema: object with subject and two sections (industry_news, research_papers); "
                    "each section has intro and a subsections list with 1-5 items; "
                    "each subsection has title, body, links. Never emit an empty subsections list."
                ),
            },
            {
                "role": "user",
                "content": (
                    "The following text was intended as JSON for a newsletter but is invalid. "
                    "Fix escaping and structure so it parses and passes schema validation. "
                    "Preserve all field values when possible.\n"
                    + (f"Validation error to fix: {previous_error}\n" if previous_error else "")
                    + "\nBroken text:\n"
                    + broken[:120000]
                ),
            },
        ],
    )
    choice = fix.choices[0].message.content
    if not choice:
        raise RuntimeError("Empty response from model while healing JSON")
    return choice


def compose_newsletter(
    client: OpenAI,
    *,
    model: str,
    research_bundle: str,
    industry_bundle: str,
    week_hint: str,
    refine: bool = False,
    topic: TopicProfile,
) -> NewsletterDraft:
    sys_prompt = _compose_system_prompt(topic)

    user_prompt = (
        f"Week focus (hint): {week_hint}\n\n"
        "## Industry News (excerpts — use only for industry_news JSON)\n"
        f"{industry_bundle}\n\n"
        "## Research Papers (excerpts — use only for research_papers JSON)\n"
        f"{research_bundle}\n\n"
        "A track may legitimately have no excerpts this week. "
        "If a section has no excerpts, still provide at least one subsection with a short, honest status line "
        "(for example: 'No notable updates this week'). Never emit an empty subsections list.\n"
    )

    def _call(temperature: float = 0.45) -> str:
        r = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = r.choices[0].message.content
        if not content:
            raise RuntimeError("Empty completion from model")
        return content

    raw = _call()
    draft: NewsletterDraft | None = None
    previous_error = ""
    try:
        draft = _parse_newsletter_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        previous_error = str(exc)
        text = raw
        for _ in range(_MAX_REPAIR_ATTEMPTS):
            healed = _heal_json_llm(client, model, text, previous_error=previous_error)
            try:
                draft = _parse_newsletter_json(healed)
                break
            except (json.JSONDecodeError, ValueError) as exc2:
                previous_error = str(exc2)
                text = healed  # repair the latest output on the next attempt
    if draft is None:
        raise RuntimeError(
            "Model output could not be parsed or schema-validated after "
            f"{_MAX_REPAIR_ATTEMPTS} repair attempts. Last error: {previous_error[:500]}"
        )

    if refine:
        draft = _refine_pass(
            client,
            model=model,
            draft=draft,
            research_bundle=research_bundle,
            industry_bundle=industry_bundle,
            topic=topic,
        )

    return draft


def _refine_pass(
    client: OpenAI,
    *,
    model: str,
    draft: NewsletterDraft,
    research_bundle: str,
    industry_bundle: str,
    topic: TopicProfile,
) -> NewsletterDraft:
    payload = draft.model_dump()
    sys_prompt = (
        "You review a newsletter JSON draft against raw search excerpts only.\n"
        "Tasks: remove or soften any claim not clearly supported; fix link lists so every URL appears in excerpts; "
        "keep two major sections (industry_news, research_papers) with 1-5 subsections each. "
        f"{topic.compose.refine_system_extra}\n"
        "Reply with a single JSON object of the same schema only. No markdown."
    )

    user_prompt = (
        "## Industry News excerpts\n"
        f"{industry_bundle}\n\n"
        "## Research Papers excerpts\n"
        f"{research_bundle}\n\n"
        "Draft JSON to fix:\n"
        + json.dumps(payload, indent=2)[:80000]
    )
    r = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = r.choices[0].message.content
    if not content:
        return draft
    try:
        return _parse_newsletter_json(content)
    except (json.JSONDecodeError, ValueError):
        return draft


def openai_client() -> OpenAI:
    return OpenAI(
        base_url=llm_base_url(),
        api_key=llm_api_key(),
    )


def newsletter_to_json_dict(draft: NewsletterDraft) -> dict[str, Any]:
    return draft.model_dump()
