from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field

from rom_newsletter.config import api_base_url, get_token


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


def _heal_json_llm(client: OpenAI, model: str, broken: str) -> str:
    fix = client.chat.completions.create(
        model=model,
        temperature=0.1,
        messages=[
            {
                "role": "system",
                "content": "Reply with a single valid JSON object only. No markdown fence, no commentary.",
            },
            {
                "role": "user",
                "content": (
                    "The following text was intended as JSON for a newsletter but is invalid. "
                    "Fix escaping and structure so it parses. Preserve all field values when possible.\n\n"
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
) -> NewsletterDraft:
    sys_prompt = """You are an editor writing a concise weekly briefing for engineers and researchers working in reduced-order modeling (ROM), scientific machine learning (SciML), and digital twins.

Hard rules:
- Use ONLY the facts implied by the provided search excerpts. Do not invent venues, dates, product names, or paper titles that are not supported by the excerpts.
- Every substantive claim must be traceable to at least one provided URL. Prefer citing by paraphrasing the excerpt, not by guessing details.
- If excerpts are thin for one track, write shorter subsections there rather than speculating.

Subject line ("subject" field):
- Under 90 characters. Lead with the week's strongest industry/vendor angles when the Industry News excerpts support them; only foreground research paper titles when industry material is thin.
- Do NOT use generic series boilerplate such as "ROM/SciML Weekly", "ROM / SciML", or "ROM, SciML, and digital twins weekly". Write a concrete headline (e.g. product/partnership themes, simulation platforms) instead.

Structure — output a single JSON object (no markdown, no prose outside JSON) with exactly this shape (industry_news is listed first to reflect editorial priority):
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
}

Classification (pre-split inputs):
- The **Research Papers excerpts** block below is tagged from sources as academic/papers (e.g. arXiv). Base "research_papers" ONLY on that block. Do not move vendor press into research unless it clearly appears there.
- The **Industry News excerpts** block is tagged from sources as industry/vendor. Base "industry_news" ONLY on that block.
- Put arXiv/preprints and academic items under "research_papers" using only URLs from the Research Papers block.
- Put vendor press, product news, blogs, and commercial announcements under "industry_news" **only when** the excerpt clearly relates to our themes: reduced-order modeling, SciML, physics-informed / physics-based ML, neural operators / surrogates, digital or virtual twins, CAE/simulation platforms (e.g. twin builder, Omniverse, Modulus, Simcenter), or AI applied to engineering simulation / physics.
- **Omit** industry subsections about unrelated topics (e.g. pure clinical trials with no simulation angle, generic enterprise IT, consumer hardware) unless the excerpt explicitly ties to simulation, twins, or physics/CAE AI.
- Prefer fewer, stronger industry subsections over padding with weak matches.
- Each of "research_papers" and "industry_news" must have between 1 and 5 subsections (inclusive).
- Each subsection's "links" should list the 1-3 most relevant URLs from the excerpts that support it (URLs must appear in the corresponding block)."""

    user_prompt = (
        f"Week focus (hint): {week_hint}\n\n"
        "## Industry News (excerpts — use only for industry_news JSON)\n"
        f"{industry_bundle}\n\n"
        "## Research Papers (excerpts — use only for research_papers JSON)\n"
        f"{research_bundle}\n"
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
    try:
        draft = _parse_newsletter_json(raw)
    except (json.JSONDecodeError, ValueError):
        healed = _heal_json_llm(client, model, raw)
        draft = _parse_newsletter_json(healed)

    if refine:
        draft = _refine_pass(
            client,
            model=model,
            draft=draft,
            research_bundle=research_bundle,
            industry_bundle=industry_bundle,
        )

    return draft


def _refine_pass(
    client: OpenAI,
    *,
    model: str,
    draft: NewsletterDraft,
    research_bundle: str,
    industry_bundle: str,
) -> NewsletterDraft:
    payload = draft.model_dump()
    sys_prompt = """You review a newsletter JSON draft against raw search excerpts only.
Tasks: remove or soften any claim not clearly supported; fix link lists so every URL appears in excerpts; keep two major sections (industry_news, research_papers) with 1-5 subsections each. Preserve subject-line rules: no "ROM/SciML Weekly"-style boilerplate; industry-led subject when excerpts support it.
Reply with a single JSON object of the same schema only. No markdown."""

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
        base_url=api_base_url(),
        api_key=get_token(),
    )


def newsletter_to_json_dict(draft: NewsletterDraft) -> dict[str, Any]:
    return draft.model_dump()
