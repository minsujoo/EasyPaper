"""Build lightweight citation mention hints from parsed bibliography entries.

The hints let the PDF viewer connect exact paper-title mentions and common author
mentions (``Vaswani et al.`` / ``Vaswani and Shazeer``) to the same reference
cards used for numeric citations.  This module deliberately stays local and
deterministic; authoritative metadata is fetched lazily only when a reader opens
the citation card.
"""

from __future__ import annotations

import re
from typing import Dict, List


_QUOTED_TITLE_RE = re.compile(r'["“]([^"”]{12,240})["”]')
_INITIAL_SURNAME_RE = re.compile(
    r"(?:\b[A-ZÀ-Ö]\.\s*)+([A-ZÀ-Ö][A-Za-zÀ-ÖØ-öø-ÿ\-']{1,})"
)
_SURNAME_FIRST_RE = re.compile(r"^\s*([A-ZÀ-Ö][A-Za-zÀ-ÖØ-öø-ÿ\-']{1,}),\s")
_ET_AL_RE = re.compile(r"\b([A-ZÀ-Ö][A-Za-zÀ-ÖØ-öø-ÿ\-']{1,})\s+et\s+al\.?", re.I)
_YEAR_PREFIX_RE = re.compile(r"^\s*(?:\(\d{4}[a-z]?\)|\d{4}[a-z]?[.)])\s*", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[a-z0-9\)\"”])\.\s+(?=[A-ZÀ-Ö\"“])")
_VENUE_WORDS_RE = re.compile(
    r"\b(?:proceedings|conference|journal|transactions|workshop|symposium|"
    r"arxiv|press|publisher|volume|vol\.?|pages?|pp\.?|doi|http)\b",
    re.I,
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n,.;:")


def _looks_like_title(value: str) -> bool:
    value = _clean(value)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9'’:\-]*", value)
    if not 4 <= len(words) <= 30:
        return False
    if not 18 <= len(value) <= 240:
        return False
    if _VENUE_WORDS_RE.search(value) or re.search(r"\b\d{4}\b", value):
        return False
    # Author lists are comma/initial heavy; titles normally contain several
    # lowercase content words.
    if len(re.findall(r"\b[a-z][a-z'’\-]{2,}\b", value)) < 2:
        return False
    if value.count(",") >= 3:
        return False
    return True


def _extract_titles(reference: str) -> List[str]:
    candidates: List[str] = []

    for match in _QUOTED_TITLE_RE.finditer(reference):
        candidates.append(_clean(match.group(1)))

    # Sentence boundaries after a lowercase word avoid splitting initials such
    # as "A. Vaswani".  APA entries often yield "(2020). Paper title" as one
    # segment, so strip a leading year before validating it.
    for segment in _SENTENCE_SPLIT_RE.split(reference):
        segment = _YEAR_PREFIX_RE.sub("", segment)
        segment = re.sub(r"^(?:et\s+al\.?\s*)", "", segment, flags=re.I)
        if _looks_like_title(segment):
            candidates.append(_clean(segment))

    unique: List[str] = []
    seen = set()
    for title in sorted(candidates, key=len, reverse=True):
        folded = title.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        unique.append(title)
        if len(unique) == 2:
            break
    return unique


def _extract_authors(reference: str) -> List[str]:
    authors: List[str] = []

    first = _SURNAME_FIRST_RE.search(reference)
    if first:
        authors.append(first.group(1))

    et_al = _ET_AL_RE.search(reference)
    if et_al:
        authors.append(et_al.group(1))

    authors.extend(match.group(1) for match in _INITIAL_SURNAME_RE.finditer(reference))

    unique: List[str] = []
    seen = set()
    for surname in authors:
        folded = surname.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        unique.append(surname)
        if len(unique) == 3:
            break
    return unique


def build_reference_mentions(references: Dict[str, str]) -> Dict[str, dict]:
    """Return title and author hints keyed by the bibliography reference key."""
    mentions: Dict[str, dict] = {}
    for key, reference in (references or {}).items():
        titles = _extract_titles(reference)
        authors = _extract_authors(reference)
        if titles or authors:
            mentions[str(key)] = {"titles": titles, "authors": authors}
    return mentions
