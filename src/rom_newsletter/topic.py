"""Topic profile: arXiv Lucene body, theme regexes, compose copy, section labels."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rom_newsletter.config import load_env


@dataclass(frozen=True)
class ComposeTopic:
    """Variable compose prompt fragments (fixed JSON schema + hard rules stay in code)."""

    editor_intro: str
    subject_rules: str
    classification_block: str
    refine_system_extra: str


@dataclass(frozen=True)
class SectionLabels:
    industry_heading: str
    research_heading: str


@dataclass(frozen=True)
class TopicProfile:
    """Loaded from ``topic.json`` or :func:`default_topic_profile`."""

    name: str
    arxiv_search_query: str | None
    theme_patterns: list[tuple[re.Pattern[str], int]]
    theme_disabled: bool
    compose: ComposeTopic
    sections: SectionLabels
    buttondown_fallback_subject: str


def _compile_theme_patterns(raw: list[dict[str, Any]]) -> list[tuple[re.Pattern[str], int]]:
    out: list[tuple[re.Pattern[str], int]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        pat = row.get("pattern")
        w = row.get("weight")
        if not isinstance(pat, str) or not pat.strip():
            continue
        if not isinstance(w, int):
            continue
        try:
            out.append((re.compile(pat, re.I), w))
        except re.error as e:
            raise ValueError(f"Invalid theme regex: {pat!r}: {e}") from e
    return out


def _default_rom_theme_patterns() -> list[tuple[re.Pattern[str], int]]:
    """Same as former ``relevance._WEIGHTED`` (ROM / SciML / twins / CAE)."""
    return _compile_theme_patterns(
        [
            {"pattern": r"digital\s+twins?", "weight": 3},
            {"pattern": r"virtual\s+twins?", "weight": 3},
            {"pattern": r"reduced[-\s]?order", "weight": 3},
            {"pattern": r"model\s+order\s+reduction", "weight": 3},
            {"pattern": r"scientific\s+machine\s+learning", "weight": 3},
            {"pattern": r"physics[-\s]?informed", "weight": 3},
            {"pattern": r"physics\s+ai|ai\s+physics", "weight": 3},
            {"pattern": r"\bpinn\b|physics[-\s]?informed\s+neural", "weight": 3},
            {"pattern": r"\bfno\b|fourier\s+neural\s+operator", "weight": 3},
            {"pattern": r"\bdeeponet\b|neural\s+operator", "weight": 3},
            {"pattern": r"surrogate\s+model", "weight": 3},
            {"pattern": r"operator\s+learning", "weight": 3},
            {"pattern": r"\bsciml\b|sci[-\s]?ml", "weight": 3},
            {"pattern": r"\bpod\b|proper\s+orthogonal\s+decomposition", "weight": 2},
            {"pattern": r"\brom\b", "weight": 2},
            {"pattern": r"physics[-\s]based\s+simulation|multiphysics", "weight": 2},
            {"pattern": r"\bcae\b|finite\s+element|\bcfd\b", "weight": 2},
            {"pattern": r"simcenter|twin\s+builder|omniverse|modulus", "weight": 2},
            {"pattern": r"predictive\s+simulation|simulation\s+software", "weight": 2},
            {"pattern": r"physics\s+ml|physics[-\s]based\s+ml", "weight": 2},
            {"pattern": r"surrogate|emulator", "weight": 2},
            {"pattern": r"calibration|parameter\s+inference", "weight": 1},
            {"pattern": r"\bai[-\s]driven\s+engineering|engineering\s+ai", "weight": 1},
            {"pattern": r"simulation|numerical\s+model", "weight": 1},
        ]
    )


def _parse_arxiv(data: dict[str, Any]) -> str | None:
    ax = data.get("arxiv")
    if ax is None:
        return None
    if not isinstance(ax, dict):
        raise ValueError("topic.json: \"arxiv\" must be null or an object")
    sq = ax.get("search_query")
    if sq is None:
        return None
    if not isinstance(sq, str) or not sq.strip():
        return None
    return sq.strip()


def _parse_compose(data: dict[str, Any]) -> ComposeTopic:
    c = data.get("compose")
    if not isinstance(c, dict):
        raise ValueError("topic.json: missing object \"compose\"")
    for key in ("editor_intro", "subject_rules", "classification_block", "refine_system_extra"):
        v = c.get(key)
        if not isinstance(v, str):
            raise ValueError(f'topic.json: compose.{key} must be a string')
    return ComposeTopic(
        editor_intro=c["editor_intro"].strip(),
        subject_rules=c["subject_rules"].strip(),
        classification_block=c["classification_block"].strip(),
        refine_system_extra=c["refine_system_extra"].strip(),
    )


def _parse_sections(data: dict[str, Any]) -> SectionLabels:
    s = data.get("sections")
    if not isinstance(s, dict):
        raise ValueError("topic.json: missing object \"sections\"")
    ih = s.get("industry_heading")
    rh = s.get("research_heading")
    if not isinstance(ih, str) or not ih.strip():
        raise ValueError('topic.json: sections.industry_heading must be a non-empty string')
    if not isinstance(rh, str) or not rh.strip():
        raise ValueError('topic.json: sections.research_heading must be a non-empty string')
    return SectionLabels(
        industry_heading=ih.strip(),
        research_heading=rh.strip(),
    )


def parse_topic_json(text: str) -> TopicProfile:
    """Parse ``topic.json`` text into a :class:`TopicProfile`."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("topic.json must be a JSON object")
    ver = data.get("version", 1)
    if ver != 1:
        raise ValueError(f"Unsupported topic.json version: {ver}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError('topic.json: "name" must be a non-empty string')

    arxiv_search_query = _parse_arxiv(data)

    th = data.get("theme")
    if not isinstance(th, dict):
        raise ValueError("topic.json: missing object \"theme\"")
    theme_disabled = bool(th.get("disabled"))
    patterns_raw = th.get("patterns")
    if theme_disabled:
        theme_patterns: list[tuple[re.Pattern[str], int]] = []
    elif isinstance(patterns_raw, list) and patterns_raw:
        theme_patterns = _compile_theme_patterns(patterns_raw)
    else:
        theme_patterns = _default_rom_theme_patterns()

    compose = _parse_compose(data)
    sections = _parse_sections(data)

    bd = data.get("buttondown")
    if not isinstance(bd, dict):
        raise ValueError("topic.json: missing object \"buttondown\"")
    fb = bd.get("fallback_subject")
    if not isinstance(fb, str) or not fb.strip():
        raise ValueError('topic.json: buttondown.fallback_subject must be a non-empty string')

    return TopicProfile(
        name=name.strip(),
        arxiv_search_query=arxiv_search_query,
        theme_patterns=theme_patterns,
        theme_disabled=theme_disabled,
        compose=compose,
        sections=sections,
        buttondown_fallback_subject=fb.strip(),
    )


def load_topic(path: Path) -> TopicProfile:
    """Load topic profile from a JSON file."""
    text = path.read_text(encoding="utf-8")
    return parse_topic_json(text)


def default_topic_profile() -> TopicProfile:
    """Prefer ``<repo>/topic.json`` if present; otherwise built-in ROM defaults."""
    root = Path(__file__).resolve().parent.parent.parent
    topic_path = root / "topic.json"
    if topic_path.is_file():
        return load_topic(topic_path)
    return _builtin_default_topic_profile()


def resolve_topic_path(project_root: Path, cli_topic: Path | None) -> Path | None:
    """Return path to topic file to load, or ``None`` to use built-in defaults only."""
    load_env()
    env = os.environ.get("ROM_NEWSLETTER_TOPIC", "").strip()
    if cli_topic is not None:
        return cli_topic
    if env:
        return Path(env)
    p = project_root / "topic.json"
    return p if p.is_file() else None


def load_topic_for_run(project_root: Path, cli_topic: Path | None) -> TopicProfile:
    """Resolve env / CLI / default path and load topic, or parse embedded defaults."""
    path = resolve_topic_path(project_root, cli_topic)
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Topic file not found: {path}")
        return load_topic(path)
    return _builtin_default_topic_profile()


def _builtin_default_topic_profile() -> TopicProfile:
    """Hard-coded defaults matching shipped ``topic.json`` (no file on disk)."""
    arxiv_search_query = (
        "("
        'all:"reduced order" OR all:"reduced-order" OR all:"model order reduction" OR all:ROM OR '
        'all:"scientific machine learning" OR all:SciML OR all:"Physics AI" OR all:"AI Physics" OR '
        'all:"operator learning" OR all:"neural operator" OR all:"surrogate model" OR '
        'all:"engineering simulation" OR all:CAE OR all:"digital twin" OR all:"digital twins" OR '
        'all:"robotics simulation" OR all:"physical AI" OR all:"robot learning" OR '
        "cat:cs.LG OR cat:cs.CE OR cat:cs.NA OR cat:physics.comp-ph OR cat:math.NA"
        ")"
    )
    compose = ComposeTopic(
        editor_intro=(
            "You are an editor writing a concise weekly briefing for engineers and researchers "
            "working in reduced-order modeling (ROM), scientific machine learning (SciML), and digital twins."
        ),
        subject_rules=(
            'Subject line ("subject" field):\n'
            "- Under 90 characters. Lead with the week's strongest industry/vendor angles when the Industry News excerpts support them; only foreground research paper titles when industry material is thin.\n"
            '- Do NOT use generic series boilerplate such as "ROM/SciML Weekly", "ROM / SciML", or "ROM, SciML, and digital twins weekly". '
            "Write a concrete headline (e.g. product/partnership themes, simulation platforms) instead."
        ),
        classification_block=(
            "Classification (pre-split inputs):\n"
            '- The **Research Papers excerpts** block below is tagged from sources as academic/papers (e.g. arXiv). Base "research_papers" ONLY on that block. Do not move vendor press into research unless it clearly appears there.\n'
            '- The **Industry News excerpts** block is tagged from sources as industry/vendor. Base "industry_news" ONLY on that block.\n'
            '- Put arXiv/preprints and academic items under "research_papers" using only URLs from the Research Papers block.\n'
            '- Put vendor press, product news, blogs, and commercial announcements under "industry_news" **only when** the excerpt clearly relates to our themes: reduced-order modeling, SciML, physics-informed / physics-based ML, neural operators / surrogates, digital or virtual twins, CAE/simulation platforms (e.g. twin builder, Omniverse, Modulus, Simcenter), or AI applied to engineering simulation / physics.\n'
            "- **Omit** industry subsections about unrelated topics (e.g. pure clinical trials with no simulation angle, generic enterprise IT, consumer hardware) unless the excerpt explicitly ties to simulation, twins, or physics/CAE AI.\n"
            "- Prefer fewer, stronger industry subsections over padding with weak matches.\n"
            '- Each of "research_papers" and "industry_news" must have between 1 and 5 subsections (inclusive).\n'
            '- Each subsection\'s "links" should list the 1-3 most relevant URLs from the excerpts that support it (URLs must appear in the corresponding block).'
        ),
        refine_system_extra=(
            'Preserve subject-line rules: no "ROM/SciML Weekly"-style boilerplate; industry-led subject when excerpts support it.'
        ),
    )
    return TopicProfile(
        name="rom-sciml-twins",
        arxiv_search_query=arxiv_search_query,
        theme_patterns=_default_rom_theme_patterns(),
        theme_disabled=False,
        compose=compose,
        sections=SectionLabels(
            industry_heading="Industry News",
            research_heading="Research Papers",
        ),
        buttondown_fallback_subject="Weekly — simulation AI & digital twins",
    )
