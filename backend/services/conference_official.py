"""Daily, conservative refreshes from conference-owned web pages.

The user spreadsheet defines which conference series matter and their local
priority.  This module only augments dates and links.  A scraped date is used
only when the official page also contains the expected conference identity and
year; failures never erase the spreadsheet value.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from collections import Counter, defaultdict
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_CATALOG_PATH = _DATA_DIR / "conferences.json"
_SITES_PATH = _DATA_DIR / "conference_official_sites.json"
_CACHE_NAME = "conference_official_cache.json"
_REFRESH_INTERVAL = timedelta(hours=24)
_MAX_HTML_BYTES = 3 * 1024 * 1024
_STARTUP_DELAY_SECONDS = 35


class _TextBlocks(HTMLParser):
    _BLOCK_TAGS = {"title", "h1", "h2", "h3", "h4", "p", "li", "td", "th", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag in self._BLOCK_TAGS and self._parts:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data.strip())

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        value = re.sub(r"\s+", " ", unescape(" ".join(self._parts))).strip()
        if value:
            self.blocks.append(value)
        self._parts.clear()


@lru_cache(maxsize=1)
def _catalog() -> list[dict]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8")).get("conferences") or []


@lru_cache(maxsize=1)
def _sites() -> dict:
    return json.loads(_SITES_PATH.read_text(encoding="utf-8")).get("sites") or {}


def _cache_path() -> Path:
    root = Path(os.getenv("EASYPAPER_CONFIG_DIR") or _DATA_DIR.parent)
    root.mkdir(parents=True, exist_ok=True)
    return root / _CACHE_NAME


@lru_cache(maxsize=1)
def _read_cache() -> dict:
    try:
        value = json.loads(_cache_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {"updated_at": "", "items": {}}


def _write_cache(value: dict) -> None:
    path = _cache_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    _read_cache.cache_clear()


def _parsed_timestamp(value: object) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def official_url_for(conference: dict) -> tuple[str, Optional[int]]:
    site = _sites().get(str(conference.get("title") or "")) or {}
    year = str(conference.get("year") or "")
    exact = str((site.get("years") or {}).get(year) or "").strip()
    if exact:
        return exact, int(year) if year.isdigit() else None
    template = str(site.get("year_url_template") or "").strip()
    if template and year.isdigit():
        return template.format(year=int(year), yy=int(year) % 100), int(year)
    return str(site.get("series_url") or "").strip(), None


def official_registration_url_for(conference: dict) -> str:
    """Return a curated year-specific registration page when one is known.

    Discovery remains the default.  This small override layer handles official
    sites whose menus point straight to a JavaScript vendor, hide the page from
    navigation, or intermittently block automated requests.
    """
    site = _sites().get(str(conference.get("title") or "")) or {}
    year = str(conference.get("year") or "")
    return str((site.get("registration_urls") or {}).get(year) or "").strip()


def official_cache_info() -> dict:
    cache = _read_cache()
    return {
        "official_updated_at": cache.get("updated_at") or "",
        "official_checked": len(cache.get("items") or {}),
    }


def official_metadata_for(conference: dict) -> dict:
    url, link_year = official_url_for(conference)
    cached = (_read_cache().get("items") or {}).get(str(conference.get("id") or "")) or {}
    return {"official_url": url, "official_link_year": link_year, **cached}


def _shifted_reference_date(value: object, reference_year: int, target_year: int) -> str:
    try:
        source = date.fromisoformat(str(value or ""))
    except ValueError:
        return ""
    shifted_year = source.year + target_year - reference_year
    try:
        return source.replace(year=shifted_year).isoformat()
    except ValueError:
        # Keep recurring February dates useful across leap/non-leap years.
        return source.replace(year=shifted_year, day=28).isoformat()


def estimated_schedule_for(conference: dict) -> dict:
    """Estimate missing dates from the closest known edition of the series."""
    title = str(conference.get("title") or "")
    target_year = int(conference.get("year") or 0)
    if not title or not target_year:
        return {}
    references = []
    cache_items = _read_cache().get("items") or {}
    for record in _catalog():
        if str(record.get("title") or "") != title:
            continue
        reference = dict(record)
        cached = cache_items.get(str(record.get("id") or "")) or {}
        if cached.get("official_verified"):
            for field in (
                "deadline", "date", "date_end", "place",
                "author_registration_deadline", "early_registration_deadline",
                "registration_deadline", "registration_url", "registration_open",
            ):
                if cached.get(field):
                    reference[field] = cached[field]
        references.append(reference)
    references.sort(key=lambda item: (
        int(item.get("year") or 0) > target_year,
        abs(target_year - int(item.get("year") or 0)),
    ))

    estimates = {}
    for field in (
        "deadline", "date", "date_end", "author_registration_deadline",
        "early_registration_deadline", "registration_deadline",
    ):
        current = str(conference.get(field) or "")
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", current):
            continue
        for reference in references:
            reference_year = int(reference.get("year") or 0)
            if not reference_year or reference_year == target_year:
                continue
            predicted = _shifted_reference_date(reference.get(field), reference_year, target_year)
            if not predicted:
                continue
            partial = re.fullmatch(r"(20\d{2}-\d{2})-\?\?", current)
            if partial and not predicted.startswith(f"{partial.group(1)}-"):
                continue
            estimates[field] = predicted
            estimates[f"{field}_based_on_year"] = reference_year
            break
    return estimates


def rolling_conference_catalog(today: date | None = None) -> list[dict]:
    """Return current/future rows and synthesize the next recurring edition.

    The spreadsheet supplies the conference family, priority, and observed
    cadence.  Once its last listed edition reaches the current year, one next
    edition is generated.  This keeps the installed catalog useful in future
    years without retaining historical cards in the UI.
    """
    today = today or date.today()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in _catalog():
        if record.get("title") and record.get("year"):
            grouped[str(record["title"])].append(record)

    records: list[dict] = []
    for title, editions in grouped.items():
        editions.sort(key=lambda item: int(item.get("year") or 0))
        records.extend(dict(item) for item in editions if int(item.get("year") or 0) >= today.year)
        years = sorted({int(item.get("year") or 0) for item in editions if item.get("year")})
        latest_year = years[-1]
        if latest_year > today.year:
            continue
        gaps = [right - left for left, right in zip(years, years[1:]) if 1 <= right - left <= 4]
        if gaps:
            counts = Counter(gaps)
            cadence = max(counts, key=lambda gap: (counts[gap], gap == gaps[-1], -gap))
        else:
            cadence = 1
        next_year = latest_year + cadence
        while next_year <= today.year:
            next_year += cadence
        template = dict(editions[-1])
        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "conference"
        template.update({
            "id": f"rolling-{slug}-{next_year}", "year": next_year,
            "date": "", "deadline": "", "abstract_deadline": "",
            "author_registration_deadline": "", "early_registration_deadline": "",
            "registration_deadline": "", "registration_url": "", "registration_open": False,
            "timezone": "", "place": "", "url": "", "rank": "",
            "tentative": True, "generated": True,
            "source": "공식 사이트 자동 회차", "source_row": None,
        })
        records.append(template)
    return records


_MONTH = r"(?:Jan(?:uary)?\.?|Feb(?:ruary)?\.?|Mar(?:ch)?\.?|Apr(?:il)?\.?|May|Jun(?:e)?\.?|Jul(?:y)?\.?|Aug(?:ust)?\.?|Sep(?:tember)?\.?|Sept\.?|Oct(?:ober)?\.?|Nov(?:ember)?\.?|Dec(?:ember)?\.?)"
_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", re.I),
    re.compile(rf"\b({_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s+(?:20)?\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:,)?\s+(?:20)?\d{{2}})\b", re.I),
)

_DATE_RANGE_PATTERNS = (
    re.compile(
        rf"\b(?:from\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:to|[-–—~])\s+"
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})(?:,)?\s+((?:20)?\d{{2}})\b",
        re.I,
    ),
    re.compile(
        rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|[-–—~])\s*"
        rf"(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+((?:20)?\d{{2}})\b",
        re.I,
    ),
    re.compile(
        rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:to|[-–—~])\s+"
        rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*"
        rf"({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+((?:20)?\d{{2}})\b",
        re.I,
    ),
)


def _date_candidates(text: str, target_year: int) -> list[date]:
    values: list[date] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            token = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", match.group(1), flags=re.I)
            try:
                parsed = date_parser.parse(token, fuzzy=False, dayfirst=bool(re.match(r"\d", token))).date()
            except (ValueError, OverflowError):
                continue
            if target_year - 1 <= parsed.year <= target_year:
                values.append(parsed)
    return list(dict.fromkeys(values))


def _date_range(text: str, target_year: int) -> tuple[str, str]:
    for index, pattern in enumerate(_DATE_RANGE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        if index == 0:
            start_day, end_day, month, year = match.groups()
            start_month = end_month = month
        elif index == 1:
            month, start_day, end_day, year = match.groups()
            start_month = end_month = month
        else:
            start_month, start_day, end_month, end_day, year = match.groups()
        try:
            start = date_parser.parse(f"{start_month} {start_day}, {year}", fuzzy=False).date()
            end = date_parser.parse(f"{end_month} {end_day}, {year}", fuzzy=False).date()
        except (ValueError, OverflowError):
            continue
        if start.year == target_year and end.year == target_year and start <= end:
            return start.isoformat(), end.isoformat()
    return "", ""


def _identity_verified(blocks: list[str], conference: dict) -> bool:
    year = str(conference.get("year") or "")
    text = " ".join(blocks).casefold()
    if year not in text:
        return False
    title = str(conference.get("title") or "").casefold()
    if len(re.sub(r"\W", "", title)) >= 3 and title in text:
        return True
    ignored = {"international", "conference", "annual", "symposium", "acm", "ieee", "meeting", "the", "and", "on", "of"}
    words = [word for word in re.findall(r"[a-z]{4,}", str(conference.get("description") or "").casefold()) if word not in ignored]
    return sum(word in text for word in words[:8]) >= min(2, len(words)) if words else False


def _json_ld_events(html: str) -> list[dict]:
    events = []
    pattern = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
    for match in pattern.finditer(html):
        try:
            value = json.loads(unescape(match.group(1)).strip())
        except (TypeError, ValueError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, dict) and str(item.get("@type") or "").casefold() in {"event", "educationevent"}:
                events.append(item)
            elif isinstance(item, dict):
                graph = item.get("@graph") or []
                events.extend(node for node in graph if isinstance(node, dict) and str(node.get("@type") or "").casefold() in {"event", "educationevent"})
    return events


def _related_schedule_urls(html: str, base_url: str, target_year: int, limit: int = 5) -> list[str]:
    """Pick official paper, schedule, and attendee-registration pages."""
    candidates: dict[str, list[tuple[int, str]]] = {"paper": [], "registration": [], "schedule": []}
    base_host = (urlparse(base_url).hostname or "").casefold()
    pattern = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    for match in pattern.finditer(html):
        href = unescape(match.group(1)).strip()
        label = re.sub(r"<[^>]+>", " ", unescape(match.group(2)))
        joined = urljoin(base_url, href)
        parsed = urlparse(joined)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = (parsed.hostname or "").casefold()
        same_site = host == base_host or host.endswith(f".{base_host}") or base_host.endswith(f".{host}")
        text = f"{label} {href}".casefold()
        year_score = 90 if str(target_year) in text else 0
        site_penalty = 0 if same_site else -100
        clean_url = joined.split("#", 1)[0]
        if re.search(r"registration|register(?:-to-attend)?|attending", text):
            candidates["registration"].append((year_score + 100 + site_penalty, clean_url))
        if re.search(r"important.?dates?|dates?.?deadlines?|paper.?submission|call.?for.?papers|deadlines?", text):
            candidates["paper"].append((year_score + 75 + site_penalty, clean_url))
        elif re.search(r"authors?|submission", text):
            candidates["paper"].append((year_score + 35 + site_penalty, clean_url))
        if re.search(r"program|schedule|conference.?dates?|important.?dates?", text):
            candidates["schedule"].append((year_score + 65 + site_penalty, clean_url))

    selected = []
    for kind, count in (("paper", 2), ("schedule", 1), ("registration", 2)):
        ranked = sorted(candidates[kind], reverse=True)
        selected.extend([url for score, url in ranked if score >= 60][:count])
    return list(dict.fromkeys(selected))[:limit]


def _iso_date(value: object, target_year: int) -> str:
    try:
        parsed = date_parser.parse(str(value or ""), fuzzy=False).date()
    except (ValueError, TypeError, OverflowError):
        return ""
    return parsed.isoformat() if parsed.year == target_year else ""


def _deadline_from_blocks(blocks: list[str], target_year: int) -> tuple[str, str, int]:
    best: tuple[int, date, str] | None = None
    for block in blocks:
        lower = block.casefold()
        if "deadline" not in lower and "due" not in lower:
            continue
        score = 0
        if re.search(r"(?:full\s+)?paper\s+submission\s+deadline", lower): score += 80
        elif "submission deadline" in lower: score += 65
        elif "paper deadline" in lower or "full paper" in lower: score += 55
        elif "submission" in lower: score += 40
        if "abstract" in lower: score -= 20
        if re.search(r"camera.?ready|notification|rebuttal|workshop|tutorial|proposal|registration", lower): score -= 70
        if re.search(r"late.?breaking|demo|poster|challenge|doctoral|student consortium", lower): score -= 55
        for parsed in _date_candidates(block, target_year):
            # Some venues publish two submission cycles.  When both blocks are
            # equally explicit, prefer the later cycle instead of biasing toward
            # the previous calendar year.
            candidate_score = score + 5
            if candidate_score >= 45 and (
                best is None or candidate_score > best[0]
                or (candidate_score == best[0] and parsed > best[1])
            ):
                best = (candidate_score, parsed, block[:500])
    return (best[1].isoformat(), best[2], best[0]) if best else ("", "", 0)


_REGISTRATION_NO_YEAR_PATTERNS = (
    re.compile(rf"\b({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I),
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({_MONTH})\b", re.I),
)


def _registration_date_candidates(text: str, target_year: int) -> list[date]:
    values = _date_candidates(text, target_year)
    for index, pattern in enumerate(_REGISTRATION_NO_YEAR_PATTERNS):
        for match in pattern.finditer(text):
            month, day = match.groups() if index == 0 else (match.group(2), match.group(1))
            token = f"{month.replace('.', '')} {day}, {target_year}"
            try:
                parsed = date_parser.parse(token, fuzzy=False).date()
            except (ValueError, OverflowError):
                continue
            values.append(parsed)
    start, end = _date_range(text, target_year)
    for value in (start, end):
        if value:
            values.append(date.fromisoformat(value))
    return list(dict.fromkeys(values))


def _registration_label(block: str) -> str:
    lower = block.casefold().strip()
    labels = []
    short = len(lower) <= 90
    author_registration = bool(
        re.search(r"\bauthor(?:s)?\s+registration\b|\bregistration\s+(?:for\s+)?authors?\b", lower)
        or (short and re.match(r"author(?:s)?\b", lower))
        or re.search(r"\bauthor(?:s)?\b.{0,180}\bregister(?:ed|ing)?\b.{0,80}\b(?:by|before|deadline|due)\b", lower)
    )
    if author_registration and "non-author" not in lower and ("workshop" not in lower or "main conference" in lower):
        labels.append("author_registration_deadline")
    if re.search(r"\bearly(?:[ -]bird)?\s+registration\b|\badvance(?:d)?\s+registration\b", lower) or (
        short and re.match(r"(?:early(?:[ -]bird)?|advance(?:d)?)\b", lower)
    ):
        labels.append("early_registration_deadline")
    if re.search(r"\bon.?site\s+(?:reg(?:istration|\.)?)\b", lower) or (
        short and re.match(r"on.?site\b", lower)
    ):
        labels.append("onsite_registration_deadline")
    elif not author_registration and not ("author" in lower and "/" in lower) and (
        re.search(r"\b(?:regular|standard|non-author)\s+registration\b", lower)
        or (
            short and (
                re.match(r"(?:regular|standard|non-author)\b", lower)
                or re.match(r"regu\.(?:\s|$)", lower)
            )
        )
    ):
        labels.append("registration_deadline")
    return labels[0] if len(set(labels)) == 1 else ""


def _registration_table_dates(blocks: list[str], target_year: int) -> dict[str, tuple[date, str]]:
    """Recover header/date columns from registration fee tables."""
    recovered: dict[str, tuple[date, str]] = {}
    for start in range(len(blocks)):
        labels = []
        cursor = start
        while cursor < min(len(blocks), start + 10):
            label = _registration_label(blocks[cursor]) if len(blocks[cursor]) <= 90 else ""
            if label:
                if label not in labels:
                    labels.append(label)
                cursor += 1
                continue
            if labels:
                break
            cursor += 1
        if len(labels) < 2:
            continue
        dated = []
        while cursor < min(len(blocks), start + 20) and len(dated) < len(labels):
            candidates = _registration_date_candidates(blocks[cursor], target_year)
            if candidates:
                dated.append((candidates[-1], blocks[cursor][:500]))
            cursor += 1
        if len(dated) < len(labels):
            continue
        for label, value in zip(labels, dated):
            if label not in recovered:
                recovered[label] = value
    return recovered


def extract_official_registration(html: str, conference: dict, page_url: str = "") -> dict:
    parser = _TextBlocks()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    blocks = parser.blocks
    target_year = int(conference.get("year") or 0)
    verified = bool(target_year and _identity_verified(blocks, conference))
    result = {
        "registration_verified": False,
        "registration_url": "", "registration_open": False,
        "author_registration_deadline": "", "early_registration_deadline": "",
        "registration_deadline": "", "registration_evidence": "",
    }
    if not verified:
        return result

    page_text = " ".join(blocks).casefold()
    # Navigation menus often put a bare "Registration" item near the top of
    # every page.  Treating that as page identity polluted deadlines with
    # author-notification, video-access, and refund dates from unrelated pages.
    registration_schedule_blocks = sum(
        bool(_registration_label(block) and _registration_date_candidates(block, target_year))
        for block in blocks
    )
    registration_page = bool(re.search(
        r"registr(?:ation|ations?|er)|attending",
        urlparse(page_url).path.casefold(),
    ) or (blocks and "registration" in blocks[0].casefold()) or registration_schedule_blocks >= 2)
    if not registration_page:
        return result
    result["registration_verified"] = True
    result["registration_url"] = page_url
    open_marker = re.search(
        r"registration is open|registration is now live|registrations are open|register now|register here|register to attend|"
        r"registration form|registration (?:site|system)(?:s)? (?:is |are )?now available|"
        r"registration.{0,60}sites? are now available|"
        r"registration system(?: is)? available|access the registration system|go to registration|to register follow",
        page_text,
    )
    closed_marker = re.search(r"registration is closed|registrations are closed|registration has closed", page_text)
    result["registration_open"] = bool(open_marker and not closed_marker)

    best: dict[str, tuple[int, date, str]] = {}
    joined_blocks = []
    for index in range(len(blocks) - 1):
        current, following = blocks[index], blocks[index + 1]
        current_label = _registration_label(current)
        # Join a standalone label to the following date, but never merge two
        # pricing categories or a category that already owns its own date.
        if current_label and (
            _registration_date_candidates(current, target_year)
            or _registration_label(following)
        ):
            continue
        joined_blocks.append(f"{current} {following}")
    context_blocks = [*blocks, *joined_blocks]
    for block in context_blocks:
        lower = block.casefold()
        if not re.search(r"registr|author|early|advance|regular|standard|on.?site|non-author|due date", lower):
            continue
        field = _registration_label(block)
        if not field:
            # Adjacent blocks are intentionally joined so labels and dates can
            # be paired, but a joined "Early ... Author ..." block is not a
            # generic registration deadline.  Let the individual blocks (or
            # the table recovery pass) classify those category-specific dates.
            has_specific_category = bool(re.search(
                r"\bauthor\b|\bearly\b|\badvance\b|\bregular\b|\bregu\.|"
                r"\bstandard\b|\bnon-author\b|\bon.?site\b",
                lower,
            ))
            generic_deadline = bool(
                re.search(r"\bregistrations?\s+(?:deadline|closes?|ends?|expires?|due|until|before|by)\b", lower)
                or re.search(r"\bdeadline\s+(?:for\s+)?[^.]{0,60}\bregistrations?\b", lower)
            )
            if not has_specific_category and generic_deadline:
                field = "registration_deadline"
            else:
                continue
        original_field = field
        if field == "onsite_registration_deadline":
            field = "registration_deadline"
        score = 60
        if "registration" in lower: score += 20
        if re.search(r"deadline|due date|expires|until|before|by\s", lower): score += 20
        if re.search(r"workshop|tutorial|satellite|challenge|cancellation|refund", lower): score -= 55
        parsed_dates = _registration_date_candidates(block, target_year)
        if original_field == "onsite_registration_deadline" and len(parsed_dates) == 1 and re.search(r"\bstarting|\bopens?|\bfrom\b", lower):
            continue
        for parsed in parsed_dates:
            previous = best.get(field)
            if score >= 45 and (
                previous is None or score > previous[0]
                or (score == previous[0] and parsed > previous[1])
            ):
                best[field] = (score, parsed, block[:500])

    for recovered_field, (parsed, evidence) in _registration_table_dates(blocks, target_year).items():
        field = "registration_deadline" if recovered_field == "onsite_registration_deadline" else recovered_field
        previous = best.get(field)
        if previous is None or (recovered_field == "onsite_registration_deadline" and parsed > previous[1]):
            best[field] = (55, parsed, evidence)

    for field, (_, parsed, evidence) in best.items():
        result[field] = parsed.isoformat()
        result["registration_evidence"] = result["registration_evidence"] or evidence
    return result


def _merge_registration_candidates(candidates: list[dict]) -> dict:
    merged = {
        "registration_verified": any(value.get("registration_verified") for value in candidates),
        "registration_url": "", "registration_open": False,
        "author_registration_deadline": "", "early_registration_deadline": "",
        "registration_deadline": "", "registration_evidence": "",
    }
    for candidate in candidates:
        if not candidate.get("registration_verified"):
            continue
        merged["registration_open"] = merged["registration_open"] or bool(candidate.get("registration_open"))
        for field in (
            "registration_url", "author_registration_deadline", "early_registration_deadline",
            "registration_deadline", "registration_evidence",
        ):
            if not merged[field] and candidate.get(field):
                merged[field] = candidate[field]
    return merged


def _event_from_blocks(blocks: list[str], target_year: int) -> tuple[str, str, str]:
    excluded = re.compile(
        r"submission|notification|camera.?ready|abstract|registration|workshop|tutorial|proposal|rebuttal|deadline",
        re.I,
    )
    # A compact date range is normally the event banner itself (for example,
    # "June 15–18 2027").  Do this pass before single-date prose, while
    # excluding milestone and satellite-event ranges.
    for block in blocks:
        start, end = _date_range(block, target_year)
        if start and not excluded.search(block):
            return start, end, block[:500]

    keywords = re.compile(r"main conference|conference dates?|takes place|will be held|conference will|event dates?", re.I)
    for block in blocks:
        if not keywords.search(block) or excluded.search(block):
            continue
        candidates = [value for value in _date_candidates(block, target_year) if value.year == target_year]
        if candidates:
            return candidates[0].isoformat(), "", block[:500]
    return "", "", ""


def _validate_schedule_order(result: dict) -> dict:
    """Reject milestone dates that cannot be the main paper deadline."""
    rejected = set(result.get("rejected") or [])
    try:
        deadline = date.fromisoformat(str(result.get("deadline") or ""))
        event_date = date.fromisoformat(str(result.get("date") or ""))
    except ValueError:
        return {**result, "rejected": sorted(rejected)}
    if deadline >= event_date:
        result["deadline"] = ""
        rejected.add("deadline")
    result["rejected"] = sorted(rejected)
    return result


def extract_official_schedule(html: str, conference: dict) -> dict:
    parser = _TextBlocks()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    blocks = parser.blocks
    context_blocks = [*blocks, *(f"{blocks[index]} {blocks[index + 1]}" for index in range(len(blocks) - 1))]
    target_year = int(conference.get("year") or 0)
    verified = bool(target_year and _identity_verified(blocks, conference))
    result = {"verified": verified, "deadline": "", "date": "", "date_end": "", "place": "", "evidence": "", "confidence": 0, "rejected": []}
    if not verified:
        return result

    for event in _json_ld_events(html):
        name = str(event.get("name") or "").casefold()
        if str(target_year) not in name and str(target_year) not in json.dumps(event):
            continue
        start = _iso_date(event.get("startDate"), target_year)
        if start:
            result["date"] = start
            result["date_end"] = _iso_date(event.get("endDate"), target_year)
            location = event.get("location") or {}
            if isinstance(location, dict):
                address = location.get("address") or ""
                if isinstance(address, dict):
                    address = ", ".join(str(address.get(key) or "") for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key))
                result["place"] = str(location.get("name") or address or "")[:300]
            result["confidence"] = max(result["confidence"], 90)
            break

    deadline, evidence, score = _deadline_from_blocks(context_blocks, target_year)
    if deadline:
        result.update({"deadline": deadline, "evidence": evidence, "confidence": max(result["confidence"], score)})
    if not result["date"]:
        event_date, event_end, event_evidence = _event_from_blocks(context_blocks, target_year)
        if event_date:
            result["date"] = event_date
            result["date_end"] = event_end
            result["evidence"] = result["evidence"] or event_evidence
            result["confidence"] = max(result["confidence"], 65)
    return _validate_schedule_order(result)


def _merge_schedule_candidates(candidates: list[dict]) -> dict:
    """Merge already ranked pages without letting secondary tracks win."""
    merged = {
        "verified": any(candidate.get("verified") for candidate in candidates),
        "deadline": "", "date": "", "date_end": "", "place": "",
        "evidence": "", "confidence": 0, "rejected": [],
    }
    for candidate in candidates:
        if not candidate.get("verified"):
            continue
        merged["confidence"] = max(merged["confidence"], candidate.get("confidence") or 0)
        for field in ("deadline", "date", "date_end", "place", "evidence"):
            if not merged[field] and candidate.get(field):
                merged[field] = candidate[field]
        merged["rejected"] = sorted(set(merged["rejected"]) | set(candidate.get("rejected") or []))
    return _validate_schedule_order(merged)


def _future_catalog(today: date) -> list[dict]:
    records = []
    for conference in rolling_conference_catalog(today):
        year = int(conference.get("year") or 0)
        if not (today.year <= year <= today.year + 4) or not official_url_for(conference)[0]:
            continue
        event_text = str(conference.get("date") or "")
        try:
            event_date = datetime.strptime(event_text, "%Y-%m-%d").date()
        except ValueError:
            event_date = None
        if event_date and event_date < today:
            continue
        partial = re.fullmatch(r"(20\d{2})-(\d{2})-\?\?", event_text)
        if partial and (int(partial.group(1)), int(partial.group(2))) < (today.year, today.month):
            continue
        records.append(conference)
    return records


async def refresh_official_conferences(*, force: bool = False, limit: int | None = None) -> dict:
    cache = _read_cache()
    now = datetime.now(timezone.utc)
    previous = _parsed_timestamp(cache.get("updated_at"))
    if not force and previous and now - previous < _REFRESH_INTERVAL:
        return {"refreshed": False, "reason": "not_due", **official_cache_info()}

    records = _future_catalog(now.date())
    if limit is not None:
        records = records[:max(0, limit)]
    urls = list(dict.fromkeys(official_url_for(record)[0] for record in records))
    semaphore = asyncio.Semaphore(6)
    pages: dict[str, tuple[int, str, str]] = {}
    related_by_id: dict[str, list[str]] = {}

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/131 Safari/537.36 PaperResearchWorkspace/3.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    ) as client:
        async def fetch(url: str) -> None:
            async with semaphore:
                for attempt in range(2):
                    try:
                        response = await client.get(url)
                        content_type = response.headers.get("content-type", "")
                        text = response.text[:_MAX_HTML_BYTES] if response.status_code == 200 and "html" in content_type else ""
                        pages[url] = (response.status_code, str(response.url), text)
                        if response.status_code not in {403, 429, 500, 502, 503, 504} or attempt:
                            return
                    except Exception as exc:
                        pages[url] = (0, url, str(exc))
                        if attempt:
                            return
                    await asyncio.sleep(0.4)
        await asyncio.gather(*(fetch(url) for url in urls))
        for conference in records:
            identifier = str(conference.get("id") or "")
            url, _ = official_url_for(conference)
            status_code, final_url, html = pages.get(url, (0, url, ""))
            if status_code == 200 and html:
                curated_registration = official_registration_url_for(conference)
                related_by_id[identifier] = list(dict.fromkeys([
                    *([curated_registration] if curated_registration else []),
                    *_related_schedule_urls(html, final_url, int(conference.get("year") or 0)),
                ]))
        related_urls = list(dict.fromkeys(
            related for values in related_by_id.values() for related in values if related not in pages
        ))
        await asyncio.gather(*(fetch(url) for url in related_urls))

    items = dict(cache.get("items") or {})
    verified_count = 0
    changed_count = 0
    for conference in records:
        identifier = str(conference.get("id") or "")
        url, link_year = official_url_for(conference)
        status_code, final_url, html = pages.get(url, (0, url, ""))
        related_urls = related_by_id.get(identifier) or []
        page_pairs = [(final_url, html)]
        page_pairs.extend(
            (pages[related][1], pages[related][2])
            for related in related_urls if pages.get(related, (0, "", ""))[0] == 200
        )
        related_html = [page_html for _, page_html in page_pairs[1:]]
        previous_item = dict(items.get(identifier) or {})
        item = {
            **previous_item,
            "official_url": final_url if status_code == 200 else url,
            "official_link_year": link_year,
            "official_checked_at": now.isoformat(),
            "official_http_status": status_code,
            "official_error": "" if status_code == 200 else (html[:300] or f"HTTP {status_code}"),
        }
        if status_code == 200 and html:
            # Keep pages separate and fill fields in link-priority order.  This
            # prevents a secondary call (workshop, blue-sky, tutorial, etc.)
            # from replacing the main/research-track deadline.
            candidates = [extract_official_schedule(page, conference) for page in [html, *related_html]]
            extracted = _merge_schedule_candidates(candidates)
            registration_candidates = [
                extract_official_registration(page_html, conference, page_url)
                for page_url, page_html in page_pairs
            ]
            curated_registration = official_registration_url_for(conference)
            curated_final = pages.get(curated_registration, (0, curated_registration, ""))[1] if curated_registration else ""
            curated_candidate = next((
                candidate for (page_url, _), candidate in zip(page_pairs, registration_candidates)
                if curated_final and page_url.rstrip("/") == curated_final.rstrip("/")
                and candidate.get("registration_verified")
            ), None)
            registration = _merge_registration_candidates(
                [curated_candidate] if curated_candidate else registration_candidates
            )
            extracted.update(registration)
            extracted["verified"] = bool(extracted.get("verified") or registration.get("registration_verified"))
            item.update({f"official_{key}": value for key, value in extracted.items()})
            if extracted.get("verified"):
                verified_count += 1
                year_page = next((value for value in related_urls if str(conference.get("year") or "") in value), "")
                if not link_year and year_page:
                    item["official_url"] = pages.get(year_page, (0, year_page, ""))[1]
                    item["official_link_year"] = conference.get("year")
                for field in extracted.get("rejected") or []:
                    item.pop(field, None)
                for field in (
                    "deadline", "date", "date_end", "place", "registration_url",
                    "author_registration_deadline", "early_registration_deadline",
                    "registration_deadline",
                ):
                    value = extracted.get(field)
                    if value:
                        item[field] = value
                # Once an actual registration page was verified, it is the
                # authority for registration milestones.  Remove stale values
                # from previous, overly broad extraction rules when the page
                # no longer supports them.
                if extracted.get("registration_verified") and extracted.get("registration_url"):
                    for field in (
                        "author_registration_deadline", "early_registration_deadline",
                        "registration_deadline",
                    ):
                        if not extracted.get(field):
                            item.pop(field, None)
                if extracted.get("registration_url"):
                    item["registration_open"] = bool(extracted.get("registration_open"))
                changed_fields = (
                    "deadline", "date", "date_end", "place", "registration_url",
                    "author_registration_deadline", "early_registration_deadline",
                    "registration_deadline", "registration_open",
                )
                if any(item.get(field) != previous_item.get(field) for field in changed_fields):
                    changed_count += 1
        items[identifier] = item

    cache = {"updated_at": now.isoformat(), "items": items}
    _write_cache(cache)
    return {
        "refreshed": True, "checked": len(records), "sites": len(urls),
        "pages": len(pages),
        "verified": verified_count, "changed": changed_count,
        "official_updated_at": cache["updated_at"],
    }


async def conference_refresh_loop() -> None:
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await refresh_official_conferences()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("공식 학회 일정 확인을 다음 주기로 미룹니다: %s", exc)
        await asyncio.sleep(60 * 60)
