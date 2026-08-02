"""Research utilities kept behind the compact Scholar surface."""

import asyncio
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

import httpx

from config import get_openalex_mailto, get_semantic_scholar_api_key
from services.paper_search import (
    _semantic_scholar_candidate,
    _wait_for_semantic_scholar_slot,
    recommend_semantic_scholar,
    search_semantic_scholar,
)
from services.reference_linker import resolve_reference

logger = logging.getLogger(__name__)

_UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
_S2_GRAPH_URL = "https://api.semanticscholar.org/graph/v1/paper"
_CONFERENCE_DATA = Path(__file__).resolve().parents[1] / "data" / "conferences.json"

_CONFERENCE_TOPIC_TEXT = {
    "general_ai": "인공지능 전반의 대표 학회로, 학습·추론·지능형 시스템 연구를 폭넓게 다룹니다.",
    "multiagent": "다중 에이전트와 자율 에이전트의 협력·경쟁·의사결정을 다루는 학회입니다.",
    "control": "제어 이론과 자동화, 동적 시스템의 분석·설계 연구를 다루는 학회입니다.",
    "vision": "컴퓨터 비전 분야의 주요 학회로, 영상 인식·3D 비전·생성 모델 등을 다룹니다.",
    "nlp": "자연어처리 분야의 주요 학회로, 언어 모델·번역·텍스트 이해 연구를 다룹니다.",
    "ml": "머신러닝의 이론과 방법론, 실제 응용을 폭넓게 다루는 주요 학회입니다.",
    "data": "데이터 마이닝·지식 발견·검색 및 대규모 데이터 분석을 다루는 학회입니다.",
    "database": "데이터베이스 시스템과 데이터 관리·처리 기술을 다루는 주요 학회입니다.",
    "robotics": "로봇의 인지·학습·제어·자율 행동과 실제 시스템을 다루는 주요 학회입니다.",
    "robot_learning": "로봇 학습과 지능형 제어, 실제 환경에서의 일반화를 중심으로 다루는 학회입니다.",
    "human_robot": "사람과 로봇의 상호작용·협업·사회적 수용성을 다루는 학회입니다.",
    "transport": "자율주행과 지능형 교통 시스템의 인지·예측·계획·제어를 다루는 학회입니다.",
    "hci": "사람과 컴퓨터의 상호작용, 사용자 경험과 인터페이스 연구를 다루는 주요 학회입니다.",
    "xr": "가상·증강·혼합현실의 기술과 사용자 경험을 다루는 학회입니다.",
    "graphics": "컴퓨터 그래픽스·렌더링·애니메이션·시각화 기술을 다루는 학회입니다.",
    "visualization": "데이터와 과학 정보를 효과적으로 표현하고 탐색하는 시각화 연구를 다룹니다.",
    "multimedia": "영상·음성·텍스트를 결합한 멀티미디어 분석·생성·검색을 다루는 학회입니다.",
    "multimedia_systems": "멀티미디어 전송·처리·서비스를 위한 시스템 기술을 다루는 학회입니다.",
    "signal": "음성·영상·센서 신호의 처리·분석·학습 방법을 다루는 학회입니다.",
    "electronics": "전자·정보·통신 시스템의 이론과 구현 및 응용 연구를 다루는 학회입니다.",
    "networks": "모바일·무선 네트워크와 통신 시스템의 설계·측정·응용을 다루는 학회입니다.",
    "sensor": "센서 네트워크와 임베디드 시스템의 설계·통신·실환경 응용을 다루는 학회입니다.",
    "cyberphysical": "사이버물리 시스템의 모델링·검증·제어와 안전한 구현을 다루는 학회입니다.",
    "realtime": "실시간·임베디드 시스템의 스케줄링·안전성·성능을 다루는 학회입니다.",
    "formal": "소프트웨어·하드웨어 시스템의 형식 검증과 자동 추론 기술을 다루는 학회입니다.",
    "planning": "인공지능 계획·스케줄링·의사결정 알고리즘을 다루는 전문 학회입니다.",
    "knowledge": "지식 표현·논리 추론·온톨로지와 설명 가능한 지능을 다루는 학회입니다.",
    "algorithms": "알고리즘의 이론적 성질과 계산 복잡도, 효율적인 설계를 다루는 학회입니다.",
    "optimization": "진화 계산과 메타휴리스틱 기반 탐색·최적화 방법을 다루는 학회입니다.",
    "economics": "알고리즘 게임이론·시장 설계·온라인 경제 시스템을 다루는 학회입니다.",
    "social": "소셜 네트워크와 계산사회과학의 데이터 분석·모델링을 다루는 학회입니다.",
    "web": "웹 기술과 검색·추천·지식 그래프·온라인 사회 시스템을 다루는 학회입니다.",
    "broad_it": "정보기술과 컴퓨팅 시스템의 최신 연구 및 응용을 폭넓게 다루는 학회입니다.",
}

_CONFERENCE_TOPIC = {
    "AAAI": "general_ai", "AAMAS": "multiagent", "ACC": "control", "ACCV": "vision",
    "ACL": "nlp", "AIM": "robotics", "AISTATS": "ml", "ASONAM": "social",
    "BMVC": "vision", "CAV": "formal", "CDC": "control", "CHI": "hci",
    "CIKM": "data", "COLT": "ml", "CVPR": "vision", "CoRL": "robot_learning",
    "EC": "economics", "ECAI": "general_ai", "ECCV": "vision", "EMNLP": "nlp",
    "FM": "formal", "GECCO": "optimization", "HRI": "human_robot", "HSCC": "cyberphysical",
    "I3D": "graphics", "IC2S2": "social", "ICAPS": "planning", "ICASSP": "signal",
    "ICCAS": "control", "ICCE-Asia": "electronics", "ICCPS": "cyberphysical", "ICCV": "vision",
    "ICDE": "database", "ICDM": "data", "ICEIC": "electronics", "ICIP": "signal",
    "ICLR": "ml", "ICML": "ml", "ICPR": "vision", "ICRA": "robotics",
    "ICROS-Summer": "control", "IEIE-Summer": "electronics", "IFAC": "control", "IJCAI": "general_ai",
    "IJCAR": "formal", "IPIU": "signal", "IPSN": "sensor", "IROS": "robotics",
    "ISMAR": "xr", "ITC-CSCC": "electronics", "ITSC": "transport", "IV": "transport",
    "KDD": "data", "KR": "knowledge", "L4DC": "ml", "MM": "multimedia",
    "MMsys": "multimedia_systems", "MobiCom": "networks", "MobiSys": "networks", "NeurIPS": "ml",
    "PAKDD": "data", "PKDD": "data", "RO-MAN": "human_robot", "RSS": "robotics",
    "RTAS": "realtime", "RTSS": "realtime", "RiTA": "broad_it", "SIGGRAPH": "graphics",
    "SIGGRAPH Asia": "graphics", "SIGIR": "data", "SMC": "control", "SODA": "algorithms",
    "SSRR": "robotics", "SenSys": "sensor", "UAI": "general_ai", "UR": "robotics",
    "VIS": "visualization", "VR": "xr", "VRST": "xr", "VTC-Fall": "networks",
    "VTC-Spring": "networks", "WACV": "vision", "WAFR": "robotics", "WINE": "economics",
    "WSDM": "data", "WWW": "web",
}


def _conference_about(title: object) -> str:
    return _CONFERENCE_TOPIC_TEXT.get(
        _CONFERENCE_TOPIC.get(str(title or ""), "broad_it"),
        _CONFERENCE_TOPIC_TEXT["broad_it"],
    )


def _doi_value(value: str) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip(), flags=re.I)


async def resolve_paper_pdf(paper: dict) -> dict:
    """Find a public PDF through direct metadata, Unpaywall, then broad scholarly lookup."""
    direct = str(paper.get("pdf_url") or "").strip()
    if direct:
        return {"pdf_url": direct, "source": paper.get("source") or "학술 레코드", "resolved": False}

    doi = _doi_value(paper.get("doi") or "")
    email = get_openalex_mailto().strip()
    if doi and email:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    _UNPAYWALL_URL.format(doi=quote(doi, safe="")),
                    params={"email": email},
                    headers={"User-Agent": "PaperResearchWorkspace/2.8"},
                )
            if response.status_code == 200:
                record = response.json()
                locations = [record.get("best_oa_location") or {}, *(record.get("oa_locations") or [])]
                for location in locations:
                    pdf_url = str(location.get("url_for_pdf") or "").strip()
                    if pdf_url.startswith("https://"):
                        return {
                            "pdf_url": pdf_url,
                            "source": location.get("host_type") or "Unpaywall",
                            "resolved": True,
                            "landing_page": location.get("url_for_landing_page") or "",
                        }
        except Exception as exc:
            logger.info("Unpaywall 원문 탐색 실패: %s", exc)

    title = str(paper.get("title") or "").strip()
    if title:
        citation = ". ".join(filter(None, [
            ", ".join(paper.get("authors") or []),
            f'"{title}"',
            str(paper.get("year") or ""),
            f"doi:{doi}" if doi else "",
        ]))
        try:
            resolved = await resolve_reference(citation)
            if resolved and resolved.get("pdf_url"):
                return {
                    "pdf_url": resolved["pdf_url"], "source": resolved.get("source") or "arXiv/OpenAlex",
                    "resolved": True, "landing_page": resolved.get("url") or "",
                }
        except Exception as exc:
            logger.info("공개 저장소 원문 탐색 실패: %s", exc)

    return {"pdf_url": "", "source": "", "resolved": False}


async def _s2_relation(paper_id: str, relation: str, limit: int = 15) -> list[dict]:
    api_key = get_semantic_scholar_api_key().strip()
    if not api_key:
        return []
    fields = "title,url,abstract,authors,year,venue,citationCount,externalIds,openAccessPdf,publicationDate"
    url = f"{_S2_GRAPH_URL}/{quote(paper_id, safe='')}/{relation}"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for attempt in range(2):
            await _wait_for_semantic_scholar_slot()
            response = await client.get(
                url, params={"limit": limit, "fields": fields},
                headers={"x-api-key": api_key, "User-Agent": "PaperResearchWorkspace/2.8"},
            )
            if response.status_code != 429 or attempt == 1:
                break
            await asyncio.sleep(2.0)
    if response.status_code != 200:
        raise RuntimeError(f"Semantic Scholar 관계 조회 오류 (HTTP {response.status_code})")
    key = "citedPaper" if relation == "references" else "citingPaper"
    return [
        candidate for row in (response.json().get("data") or [])
        if (candidate := _semantic_scholar_candidate(row.get(key) or {}))
    ]


def _graph_identifier(paper: dict) -> str:
    s2_id = str(paper.get("semantic_scholar_id") or "").strip()
    if s2_id:
        return s2_id
    doi = _doi_value(paper.get("doi") or "")
    arxiv_match = re.fullmatch(r"10\.48550/arxiv\.(.+)", doi, flags=re.I)
    if arxiv_match:
        return f"ARXIV:{arxiv_match.group(1)}"
    return f"DOI:{doi}" if doi else ""


async def _search_graph_seed(paper: dict) -> dict:
    """Resolve locally imported PDFs to a canonical Semantic Scholar record."""
    title = str(paper.get("title") or "").strip()
    if not title:
        return {}
    try:
        candidates = await search_semantic_scholar(title[:300], limit=5)
    except Exception as exc:
        logger.info("관계 지도 논문 식별 실패: %s", exc)
        return {}
    normalized = re.sub(r"[^\w]+", " ", title.casefold()).strip()
    ranked = sorted(
        candidates,
        key=lambda item: SequenceMatcher(
            None, normalized,
            re.sub(r"[^\w]+", " ", str(item.get("title") or "").casefold()).strip(),
        ).ratio(),
        reverse=True,
    )
    if not ranked:
        return {}
    score = SequenceMatcher(
        None, normalized,
        re.sub(r"[^\w]+", " ", str(ranked[0].get("title") or "").casefold()).strip(),
    ).ratio()
    return ranked[0] if score >= 0.45 else {}


async def build_paper_graph(paper: dict) -> dict:
    resolved = paper if paper.get("semantic_scholar_id") else await _search_graph_seed(paper)
    identifier = _graph_identifier(resolved) or _graph_identifier(paper)
    if not identifier:
        raise RuntimeError("제목으로 Semantic Scholar 논문을 식별하지 못했습니다.")

    try:
        references, citations = await asyncio.gather(
            _s2_relation(identifier, "references"),
            _s2_relation(identifier, "citations"),
        )
    except RuntimeError:
        fallback = await _search_graph_seed(paper)
        fallback_identifier = _graph_identifier(fallback)
        if not fallback_identifier or fallback_identifier == identifier:
            raise
        resolved = fallback
        identifier = fallback_identifier
        references, citations = await asyncio.gather(
            _s2_relation(identifier, "references"),
            _s2_relation(identifier, "citations"),
        )
    s2_id = str(resolved.get("semantic_scholar_id") or "").strip()
    try:
        similar = await recommend_semantic_scholar([s2_id], limit=10) if s2_id else []
    except Exception:
        similar = []

    root_id = str(paper.get("id") or s2_id or identifier)
    root = {**resolved, **paper, "id": root_id, "group": "root"}
    nodes = [root]
    edges = []
    seen = {root_id}
    for group, candidates in (("reference", references), ("citation", citations), ("similar", similar)):
        for candidate in candidates:
            node_id = str(candidate.get("id") or candidate.get("semantic_scholar_id") or "")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            nodes.append({**candidate, "id": node_id, "group": group})
            if group == "reference":
                edges.append({"source": root_id, "target": node_id, "kind": group})
            else:
                edges.append({"source": node_id, "target": root_id, "kind": group})
    return {"root_id": root_id, "nodes": nodes, "edges": edges, "total": len(nodes)}


@lru_cache(maxsize=1)
def _conference_payload() -> dict:
    try:
        return json.loads(_CONFERENCE_DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("사용자 학회 목록을 읽지 못했습니다: %s", exc)
        return {"conferences": [], "warning": "학회 목록을 읽지 못했습니다."}


def _exact_date(value: object) -> date | None:
    text = str(value or "").strip()
    if "?" in text or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _conference_action(conference: dict, today: date | None = None) -> tuple[str, int | None, str, str]:
    today = today or date.today()
    deadline = _exact_date(conference.get("deadline"))
    event_date = _exact_date(conference.get("date"))
    event_end = _exact_date(conference.get("date_end")) or event_date
    if event_date and event_end and (event_end < event_date or event_end - event_date > timedelta(days=21)):
        event_end = event_date
    if event_end and event_end < today:
        return "past", None, "", ""
    if deadline and deadline >= today:
        return "upcoming", (deadline - today).days, "paper", deadline.isoformat()

    registration_dates = sorted(filter(None, (
        _exact_date(conference.get("author_registration_deadline")),
        _exact_date(conference.get("early_registration_deadline")),
        _exact_date(conference.get("registration_deadline")),
    )))
    future_registration = next((value for value in registration_dates if value >= today), None)
    if future_registration:
        return "upcoming", (future_registration - today).days, "registration", future_registration.isoformat()
    if conference.get("registration_open") and (not event_end or event_end >= today):
        days = (event_date - today).days if event_date and event_date >= today else None
        return "upcoming", days, "registration", ""
    if event_date and event_end and event_end >= today:
        return "upcoming", max(0, (event_date - today).days), "event", event_date.isoformat()
    if deadline and deadline < today and not event_date:
        return "past", (deadline - today).days, "paper", deadline.isoformat()
    if int(conference.get("year") or 0) >= today.year:
        return "upcoming", None, "event", ""
    return "unknown", None, "", ""


def _conference_status(conference: dict, today: date | None = None) -> tuple[str, int | None]:
    status, days, _, _ = _conference_action(conference, today)
    return status, days


def _conference_sort_key(conference: dict) -> tuple:
    submission_deadline = _exact_date(conference.get("deadline"))
    ordinal = submission_deadline.toordinal() if submission_deadline else 0
    if conference.get("submission_status") == "open":
        return 0, ordinal, str(conference.get("title") or "")
    return 1, -ordinal, str(conference.get("title") or "")


def conference_source_info() -> dict:
    payload = _conference_payload()
    return {key: payload.get(key) or "" for key in (
        "source_title", "source_url", "source_modified_at", "warning",
    )}


async def list_conferences(today: date | None = None) -> list[dict]:
    from services.conference_official import (
        estimated_schedule_for,
        official_metadata_for,
        rolling_conference_catalog,
    )

    today = today or date.today()
    conferences = []
    for record in rolling_conference_catalog(today):
        conference = dict(record)
        conference["about_ko"] = _conference_about(conference.get("title"))
        official = official_metadata_for(conference)
        conference.update(official)
        if official.get("official_verified"):
            changed = False
            for field in (
                "deadline", "date", "date_end", "place", "registration_url",
                "author_registration_deadline", "early_registration_deadline",
                "registration_deadline",
            ):
                if official.get(field):
                    conference[f"sheet_{field}"] = conference.get(field) or ""
                    conference[field] = official[field]
                    changed = True
            if official.get("registration_url"):
                conference["registration_open"] = bool(official.get("registration_open"))
            conference["schedule_source"] = "official" if changed else "sheet"
        else:
            conference["schedule_source"] = "sheet"
        estimates = estimated_schedule_for(conference)
        estimated_fields = []
        for field in (
            "deadline", "date", "date_end", "author_registration_deadline",
            "early_registration_deadline", "registration_deadline",
        ):
            if (
                field in {
                    "author_registration_deadline", "early_registration_deadline",
                    "registration_deadline",
                }
                and official.get("official_registration_verified")
            ):
                continue
            if estimates.get(field):
                conference[f"sheet_{field}"] = conference.get(field) or ""
                conference[field] = estimates[field]
                conference[f"{field}_estimated"] = True
                conference[f"{field}_based_on_year"] = estimates.get(f"{field}_based_on_year")
                estimated_fields.append(field)
        # A year/month-only source is still enough for a useful estimate.  Use
        # the middle of the stated month after historical-edition matching has
        # had the first chance, and give year-only future rows a conservative
        # mid-year (or year-end for the current year) placeholder.
        for field in ("deadline", "date", "author_registration_deadline", "early_registration_deadline", "registration_deadline"):
            current = str(conference.get(field) or "")
            partial = re.fullmatch(r"(20\d{2})-(\d{2})-\?\?", current)
            fallback = ""
            if partial:
                fallback = f"{partial.group(1)}-{partial.group(2)}-15"
            elif field == "date" and not current:
                target_year = int(conference.get("year") or 0)
                if target_year > today.year:
                    fallback = f"{target_year}-07-15"
                elif target_year == today.year:
                    fallback_date = max(today, date(target_year, 12, 15))
                    fallback = fallback_date.isoformat()
            if fallback:
                conference[f"sheet_{field}"] = current
                conference[field] = fallback
                conference[f"{field}_estimated"] = True
                if field not in estimated_fields:
                    estimated_fields.append(field)
        if not _exact_date(conference.get("deadline")):
            event_for_estimate = _exact_date(conference.get("date"))
            if event_for_estimate:
                conference["sheet_deadline"] = conference.get("deadline") or ""
                conference["deadline"] = (event_for_estimate - timedelta(days=120)).isoformat()
                conference["deadline_estimated"] = True
                conference["deadline_estimate_basis"] = "event_minus_120_days"
                if "deadline" not in estimated_fields:
                    estimated_fields.append("deadline")
        conference["estimated_fields"] = estimated_fields
        if estimated_fields and conference["schedule_source"] == "sheet":
            conference["schedule_source"] = "estimated"
        status, days_remaining, next_action_kind, next_action_date = _conference_action(conference, today)
        conference["status"] = status
        conference["days_remaining"] = days_remaining
        conference["next_action_kind"] = next_action_kind
        conference["next_action_date"] = next_action_date
        submission_deadline = _exact_date(conference.get("deadline"))
        conference["submission_status"] = (
            "open" if submission_deadline and submission_deadline >= today else "closed"
        )
        conference["submission_days_remaining"] = (
            (submission_deadline - today).days if submission_deadline else None
        )
        if status != "past":
            conferences.append(conference)
    conferences.sort(key=_conference_sort_key)
    return conferences


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def scholar_timer_status() -> dict:
    if sys.platform != "linux" or not shutil_which("systemctl"):
        return {"supported": False, "enabled": False, "active": False}
    enabled = subprocess.run(
        ["systemctl", "--user", "is-enabled", "paper-scholar-crawl.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "paper-scholar-crawl.timer"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    return {"supported": True, "enabled": enabled, "active": active}


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def install_scholar_user_timer() -> dict:
    """Install a user-level persistent timer only for the packaged Linux binary."""
    if sys.platform != "linux" or not getattr(sys, "frozen", False) or not shutil_which("systemctl"):
        return scholar_timer_status()
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    env_keys = (
        "EASYPAPER_CONFIG_DIR", "DB_PATH", "UPLOAD_DIR", "CACHE_DIR", "LIBRARY_DIR",
        "DROP_STAGING_DIR", "EASYPAPER_LOG_DIR",
    )
    environment = "\n".join(
        f"Environment={_systemd_quote(f'{key}={os.environ[key]}')}"
        for key in env_keys if os.environ.get(key)
    )
    service = f"""[Unit]
Description=Collect personal research paper recommendations
After=network-online.target

[Service]
Type=oneshot
ExecStart={_systemd_quote(sys.executable)} --scholar-crawl-once
{environment}
"""
    timer = """[Unit]
Description=Refresh personal research paper recommendations every 24 hours

[Timer]
OnBootSec=10min
OnUnitActiveSec=24h
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
"""
    (unit_dir / "paper-scholar-crawl.service").write_text(service, encoding="utf-8")
    (unit_dir / "paper-scholar-crawl.timer").write_text(timer, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=10)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "paper-scholar-crawl.timer"],
        check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return scholar_timer_status()
