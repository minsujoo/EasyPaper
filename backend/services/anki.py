import html
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from services.db import db_get_meta, db_import_obsidian_vocabulary_cards


DEFAULT_ANKI_URL = "http://127.0.0.1:8765"
DEFAULT_DECK = "논문 영어"


def launch_anki() -> bool:
    """Anki를 백그라운드에서 시작한다. Anki 자체가 단일 인스턴스를 보장한다."""
    executable = shutil.which("anki")
    if not executable:
        return False
    try:
        subprocess.Popen(
            [executable],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def get_anki_config(username: str) -> Dict[str, str]:
    return {
        "url": db_get_meta(f"vocabulary:{username}:anki_url") or DEFAULT_ANKI_URL,
        "deck": db_get_meta(f"vocabulary:{username}:anki_deck") or DEFAULT_DECK,
        "api_key": db_get_meta(f"vocabulary:{username}:anki_api_key") or "",
        "obsidian_path": db_get_meta(f"vocabulary:{username}:obsidian_path") or str(
            Path.home() / "obsidian" / "Areas" / "English" / "Vocab-Deck.md"
        ),
    }


async def invoke(action: str, params: Optional[dict] = None, *, username: str) -> Any:
    config = get_anki_config(username)
    payload = {"action": action, "version": 6, "params": params or {}}
    if config["api_key"]:
        payload["key"] = config["api_key"]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(config["url"], json=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Anki에 연결할 수 없습니다. Anki가 실행 중인지 확인해 주세요.") from exc
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


async def status(username: str) -> Dict[str, Any]:
    try:
        version = await invoke("version", username=username)
        decks = await invoke("deckNames", username=username)
        return {"connected": True, "version": version, "decks": decks or []}
    except RuntimeError as exc:
        return {"connected": False, "error": str(exc), "decks": []}


def _anki_back(card: Dict[str, Any]) -> str:
    page = f"p.{card['page_num']}" if card.get("page_num") else ""
    source = " · ".join(part for part in (card.get("paper_title", ""), page) if part)
    return (
        f"<div style='font-size:1.15em'><b>{html.escape(card['meaning_ko'])}</b></div>"
        f"<hr><div>{html.escape(card.get('context_en', ''))}</div>"
        f"<div style='margin-top:8px;color:#555'>{html.escape(card.get('context_ko', ''))}</div>"
        f"<div style='margin-top:12px;font-size:.8em;color:#888'>{html.escape(source)}</div>"
    )


async def sync_card(card: Dict[str, Any], username: str) -> int:
    config = get_anki_config(username)
    await invoke("createDeck", {"deck": config["deck"]}, username=username)
    # 앱 내부 고유 ID 태그를 이용하면 같은 카드를 여러 번 보내도 복제되지 않는다.
    tag = f"paper_vocab_id_{card['id']}"
    existing = await invoke("findNotes", {"query": f"tag:{tag}"}, username=username)
    if existing:
        return int(existing[0])
    note = {
        "deckName": config["deck"],
        "modelName": "Basic",
        "fields": {"Front": html.escape(card["term"]), "Back": _anki_back(card)},
        "options": {"allowDuplicate": False},
        "tags": ["paper_vocab", tag],
    }
    note_id = await invoke("addNote", {"note": note}, username=username)
    if not note_id:
        raise RuntimeError("Anki가 카드를 추가하지 못했습니다.")
    return int(note_id)


_OBSIDIAN_CARD_RE = re.compile(
    r"^-\s+(.+?)::(.*?)\s+#flashcards/english/vocab\s*$"
)


def parse_obsidian_deck(path_value: str) -> list[Dict[str, str]]:
    """기존 Obsidian 플래시카드 파일을 SR 주석을 건드리지 않고 읽는다."""
    path = Path(os.path.expanduser(path_value))
    if not path.is_file():
        return []
    cards: list[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = _OBSIDIAN_CARD_RE.match(raw_line.strip())
        if match:
            if current:
                cards.append(current)
            current = {
                "term": match.group(1).strip(),
                "meaning_ko": match.group(2).strip(),
                "context_en": "",
                "context_ko": "",
            }
            continue
        if not current:
            continue
        stripped = raw_line.strip()
        if stripped.startswith("문맥:"):
            current["context_en"] = stripped[3:].strip().replace("==", "")
        elif stripped.startswith("해석:"):
            current["context_ko"] = stripped[3:].strip().replace("==", "")
    if current:
        cards.append(current)
    return cards


async def sync_obsidian_deck(username: str) -> Dict[str, int]:
    """기존 Obsidian 카드를 Anki에 중복 없이 초기 마이그레이션한다."""
    config = get_anki_config(username)
    cards = parse_obsidian_deck(config["obsidian_path"])
    if not cards:
        return {"found": 0, "imported": 0, "existing": 0, "failed": 0}
    await invoke("createDeck", {"deck": config["deck"]}, username=username)
    imported = existing_count = failed = 0
    mirrored_cards: list[Dict[str, Any]] = []
    for card in cards:
        digest = hashlib.sha1(card["term"].casefold().encode("utf-8")).hexdigest()[:16]
        tag = f"obsidian_vocab_{digest}"
        try:
            existing = await invoke("findNotes", {"query": f"tag:{tag}"}, username=username)
            if existing:
                existing_count += 1
                mirrored_cards.append({**card, "anki_note_id": int(existing[0])})
                continue
            context_en = html.escape(card["context_en"])
            context_ko = html.escape(card["context_ko"])
            back = (
                f"<div style='font-size:1.15em'><b>{html.escape(card['meaning_ko'])}</b></div>"
                f"<hr><div>{context_en}</div>"
                f"<div style='margin-top:8px;color:#555'>{context_ko}</div>"
                "<div style='margin-top:12px;font-size:.8em;color:#888'>Obsidian 단어장</div>"
            )
            note_id = await invoke("addNote", {"note": {
                "deckName": config["deck"], "modelName": "Basic",
                "fields": {"Front": html.escape(card["term"]), "Back": back},
                "options": {"allowDuplicate": False},
                "tags": ["paper_vocab", "obsidian_vocab", tag],
            }}, username=username)
            imported += 1 if note_id else 0
            failed += 0 if note_id else 1
            if note_id:
                mirrored_cards.append({**card, "anki_note_id": int(note_id)})
        except RuntimeError:
            failed += 1
    mirrored = db_import_obsidian_vocabulary_cards(username, mirrored_cards)
    return {"found": len(cards), "imported": imported, "existing": existing_count, "failed": failed, "mirrored": mirrored}


def append_obsidian_card(card: Dict[str, Any], username: str) -> bool:
    path = Path(os.path.expanduser(get_anki_config(username)["obsidian_path"]))
    if not path.is_file():
        return False
    term = card["term"].strip()
    meaning = card["meaning_ko"].strip()
    context_en = card.get("context_en", "").strip().replace(term, f"=={term}==", 1)
    context_ko = card.get("context_ko", "").strip()
    if meaning and meaning in context_ko:
        context_ko = context_ko.replace(meaning, f"=={meaning}==", 1)
    block = (
        f"\n- {term}::{meaning} #flashcards/english/vocab\n"
        f"  문맥: {context_en}\n"
        f"  해석: {context_ko}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return True
