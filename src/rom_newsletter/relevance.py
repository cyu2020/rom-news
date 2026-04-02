from __future__ import annotations

import re
from typing import Any

from rom_newsletter.search import SearchHit, merge_hits_ordered

# Weighted regexes: ROM / SciML / digital twins / physics-based simulation / CAE+AI
_WEIGHTED: list[tuple[re.Pattern[str], int]] = [
    # Strong theme anchors (3)
    (re.compile(r"digital\s+twins?", re.I), 3),
    (re.compile(r"virtual\s+twins?", re.I), 3),
    (re.compile(r"reduced[-\s]?order", re.I), 3),
    (re.compile(r"model\s+order\s+reduction", re.I), 3),
    (re.compile(r"scientific\s+machine\s+learning", re.I), 3),
    (re.compile(r"physics[-\s]?informed", re.I), 3),
    (re.compile(r"physics\s+ai|ai\s+physics", re.I), 3),
    (re.compile(r"\bpinn\b|physics[-\s]?informed\s+neural", re.I), 3),
    (re.compile(r"\bfno\b|fourier\s+neural\s+operator", re.I), 3),
    (re.compile(r"\bdeeponet\b|neural\s+operator", re.I), 3),
    (re.compile(r"surrogate\s+model", re.I), 3),
    (re.compile(r"operator\s+learning", re.I), 3),
    (re.compile(r"\bsciml\b|sci[-\s]?ml", re.I), 3),
    (re.compile(r"\bpod\b|proper\s+orthogonal\s+decomposition", re.I), 2),
    (re.compile(r"\brom\b", re.I), 2),  # word-ish; may match acronym in caps context
    # Simulation / twins / CAE (2)
    (re.compile(r"physics[-\s]based\s+simulation|multiphysics", re.I), 2),
    (re.compile(r"\bcae\b|finite\s+element|\bcfd\b", re.I), 2),
    (re.compile(r"simcenter|twin\s+builder|omniverse|modulus", re.I), 2),
    (re.compile(r"predictive\s+simulation|simulation\s+software", re.I), 2),
    (re.compile(r"physics\s+ml|physics[-\s]based\s+ml", re.I), 2),
    (re.compile(r"surrogate|emulator", re.I), 2),
    (re.compile(r"calibration|parameter\s+inference", re.I), 1),
    # Digital engineering (1) — broad; used with min_score >= 2 typically
    (re.compile(r"\bai[-\s]driven\s+engineering|engineering\s+ai", re.I), 1),
    (re.compile(r"simulation|numerical\s+model", re.I), 1),
]


def is_arxiv_hit(hit: SearchHit) -> bool:
    return "arxiv.org" in hit.url.lower()


def theme_score(
    hit: SearchHit,
    weighted: list[tuple[re.Pattern[str], int]] | None = None,
) -> int:
    """Sum of weights for regex matches. *weighted* defaults to built-in ROM/SciML patterns."""
    patterns = weighted if weighted is not None else _WEIGHTED
    if not patterns:
        return 0
    blob = f"{hit.title}\n{hit.content}\n{hit.url}\n{hit.keyword}"
    total = 0
    for pat, w in patterns:
        if pat.search(blob):
            total += w
    return total


def apply_theme_filter(
    hits: list[SearchHit],
    *,
    min_score: int,
    max_non_arxiv: int | None = 48,
    floor_non_arxiv: int = 5,
    backfill_min_score: int = 1,
    weighted_patterns: list[tuple[re.Pattern[str], int]] | None = None,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """Keep all arXiv hits; rank other hits by theme score, keep those >= min_score.

    If that leaves fewer than *floor_non_arxiv* non-arXiv hits, backfill with the next
    highest-scoring dropped items **with score >= backfill_min_score** until *floor* or
    exhausted (avoids stuffing irrelevant items just to hit a count).

    *max_non_arxiv* caps how many non-arXiv hits are passed to the composer (after sort).
    Set *min_score* to 0 to disable score filtering (ranking/cap still apply).
    """
    arxiv = [h for h in hits if is_arxiv_hit(h)]
    other = [h for h in hits if not is_arxiv_hit(h)]
    scored = [(theme_score(h, weighted_patterns), h) for h in other]
    scored.sort(key=lambda x: (-x[0], x[1].url))

    stats: dict[str, Any] = {
        "enabled": min_score > 0,
        "min_score": min_score,
        "floor_non_arxiv": floor_non_arxiv,
        "backfill_min_score": backfill_min_score,
        "non_arxiv_input": len(other),
        "arxiv_kept": len(arxiv),
    }

    if min_score <= 0:
        kept = [h for _, h in scored]
        if max_non_arxiv is not None and len(kept) > max_non_arxiv:
            kept = kept[:max_non_arxiv]
        stats["non_arxiv_kept"] = len(kept)
        stats["non_arxiv_dropped"] = 0
        stats["backfilled"] = 0
        stats["enabled"] = False
        out = merge_hits_ordered(arxiv, kept)
        non_arx_out = [h for h in out if not is_arxiv_hit(h)]
        stats["non_arxiv_sample"] = [
            {"url": h.url, "theme_score": theme_score(h, weighted_patterns)}
            for h in non_arx_out[:20]
        ]
        return out, stats

    kept: list[SearchHit] = []
    dropped: list[tuple[int, SearchHit]] = []
    for s, h in scored:
        if s >= min_score:
            kept.append(h)
        else:
            dropped.append((s, h))

    backfill = 0
    if len(kept) < floor_non_arxiv and dropped:
        dropped.sort(key=lambda x: (-x[0], x[1].url))
        seen_u = {h.url for h in kept}
        for s, h in dropped:
            if len(kept) >= floor_non_arxiv:
                break
            if s < backfill_min_score:
                continue
            if h.url not in seen_u:
                kept.append(h)
                seen_u.add(h.url)
                backfill += 1

    if max_non_arxiv is not None and len(kept) > max_non_arxiv:
        kept_scored = [(theme_score(h, weighted_patterns), h) for h in kept]
        kept_scored.sort(key=lambda x: (-x[0], x[1].url))
        kept = [h for _, h in kept_scored[:max_non_arxiv]]

    stats["non_arxiv_kept"] = len(kept)
    stats["non_arxiv_dropped"] = max(0, len(other) - len(kept))
    stats["backfilled"] = backfill
    stats["max_non_arxiv"] = max_non_arxiv

    out = merge_hits_ordered(arxiv, kept)
    non_arx_out = [h for h in out if not is_arxiv_hit(h)]
    stats["non_arxiv_sample"] = [
        {"url": h.url, "theme_score": theme_score(h, weighted_patterns)}
        for h in non_arx_out[:20]
    ]
    return out, stats
