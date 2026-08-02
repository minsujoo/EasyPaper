import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from services.anki import append_obsidian_card, get_anki_config, invoke, launch_anki, status as anki_status, sync_card, sync_obsidian_deck
from services.auth import get_current_user
from services.db import (
    db_delete_vocabulary_card,
    db_get_document,
    db_get_vocabulary_card,
    db_list_vocabulary_cards,
    db_mark_vocabulary_obsidian_synced,
    db_set_meta,
    db_update_vocabulary_sync,
    db_upsert_vocabulary_card,
)
from services.llm_client import stream_vocabulary_suggestion

router = APIRouter()


async def _ensure_anki_started(username: str) -> None:
    current = await anki_status(username)
    if current.get("connected") or not launch_anki():
        return
    # 프로필과 애드온이 로드될 시간을 주되 요청을 과도하게 오래 막지 않는다.
    for _ in range(12):
        await asyncio.sleep(0.5)
        if (await anki_status(username)).get("connected"):
            return


class SuggestRequest(BaseModel):
    doc_id: str
    term: str = Field(min_length=1, max_length=160)
    context_en: str = Field(default="", max_length=4000)
    context_ko: str = Field(default="", max_length=4000)


class CardRequest(SuggestRequest):
    page_num: Optional[int] = None
    meaning_ko: str = Field(min_length=1, max_length=1000)
    paper_title: str = Field(default="", max_length=1000)
    sync_anki: bool = True


class ConfigRequest(BaseModel):
    deck: str = Field(min_length=1, max_length=160)
    url: str = Field(default="http://127.0.0.1:8765", max_length=500)
    api_key: str = Field(default="", max_length=500)
    obsidian_path: str = Field(default="", max_length=2000)


class ReviewAnswerRequest(BaseModel):
    ease: int = Field(ge=1, le=4)


def _review_card_payload(card: Optional[dict]) -> Optional[dict]:
    if not card:
        return None
    fields = card.get("fields") or {}
    return {
        "card_id": card.get("cardId"),
        "front": str((fields.get("Front") or {}).get("value") or ""),
        "back": str((fields.get("Back") or {}).get("value") or ""),
        "buttons": card.get("buttons") or [],
        "next_reviews": card.get("nextReviews") or [],
        "deck_name": card.get("deckName") or "",
    }


async def _current_review_card(username: str) -> Optional[dict]:
    try:
        return _review_card_payload(await invoke("guiCurrentCard", username=username))
    except RuntimeError:
        return None


def _owned_doc(doc_id: str, username: str) -> dict:
    doc = db_get_document(doc_id)
    if not doc or doc.get("username") != username or doc.get("is_deleted"):
        raise HTTPException(status_code=404, detail="논문을 찾을 수 없습니다.")
    return doc


@router.get("/vocabulary")
async def list_cards(current_user: str = Depends(get_current_user)):
    return {"cards": db_list_vocabulary_cards(current_user)}


@router.post("/vocabulary/suggest")
async def suggest_card(payload: SuggestRequest, current_user: str = Depends(get_current_user)):
    doc = _owned_doc(payload.doc_id, current_user)
    chunks = []
    try:
        async for token in stream_vocabulary_suggestion(
            payload.term, payload.context_en, payload.context_ko,
            (doc.get("metadata") or {}).get("title") or doc.get("filename") or "",
            session_id=payload.doc_id,
        ):
            chunks.append(token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"단어 설명 생성 실패: {exc}")
    raw = "".join(chunks).strip()
    match = re.search(r"\{[\s\S]*\}", raw)
    try:
        result = json.loads(match.group(0) if match else raw)
    except (json.JSONDecodeError, AttributeError):
        raise HTTPException(status_code=502, detail="AI 응답을 단어 카드 형식으로 해석하지 못했습니다.")
    return {
        "meaning_ko": str(result.get("meaning_ko") or "").strip(),
        "context_ko": str(result.get("context_ko") or payload.context_ko or "").strip(),
    }


@router.post("/vocabulary")
async def save_card(payload: CardRequest, current_user: str = Depends(get_current_user)):
    _owned_doc(payload.doc_id, current_user)
    card = db_upsert_vocabulary_card(current_user, payload.model_dump())
    if not card.get("obsidian_synced"):
        try:
            if append_obsidian_card(card, current_user):
                db_mark_vocabulary_obsidian_synced(card["id"], current_user)
                card["obsidian_synced"] = 1
        except OSError:
            pass
    if payload.sync_anki:
        await _ensure_anki_started(current_user)
        try:
            note_id = await sync_card(card, current_user)
            db_update_vocabulary_sync(card["id"], current_user, "synced", note_id=note_id)
            card.update({"anki_status": "synced", "anki_note_id": note_id, "anki_error": None})
        except RuntimeError as exc:
            db_update_vocabulary_sync(card["id"], current_user, "pending", error=str(exc))
            card.update({"anki_status": "pending", "anki_error": str(exc)})
    return {"card": card}


@router.post("/vocabulary/sync")
async def sync_pending(current_user: str = Depends(get_current_user)):
    await _ensure_anki_started(current_user)
    try:
        obsidian = await sync_obsidian_deck(current_user)
    except RuntimeError as exc:
        obsidian = {"found": 0, "imported": 0, "existing": 0, "failed": 1, "error": str(exc)}
    synced, failed = 0, 0
    for card in db_list_vocabulary_cards(current_user):
        if card.get("anki_status") == "synced":
            continue
        try:
            note_id = await sync_card(card, current_user)
            db_update_vocabulary_sync(card["id"], current_user, "synced", note_id=note_id)
            synced += 1
        except RuntimeError as exc:
            db_update_vocabulary_sync(card["id"], current_user, "pending", error=str(exc))
            failed += 1
    return {"synced": synced, "failed": failed, "obsidian": obsidian}


@router.delete("/vocabulary/{card_id}")
async def delete_card(card_id: int, current_user: str = Depends(get_current_user)):
    if not db_delete_vocabulary_card(card_id, current_user):
        raise HTTPException(status_code=404, detail="단어 카드를 찾을 수 없습니다.")
    return {"ok": True}


@router.get("/vocabulary/anki/status")
async def get_status(current_user: str = Depends(get_current_user)):
    result = await anki_status(current_user)
    if not result.get("connected"):
        await _ensure_anki_started(current_user)
        result = await anki_status(current_user)
    config = get_anki_config(current_user)
    return {**result, "config": {"url": config["url"], "deck": config["deck"], "obsidian_path": config["obsidian_path"], "has_api_key": bool(config["api_key"])}}


@router.put("/vocabulary/anki/config")
async def save_config(payload: ConfigRequest, current_user: str = Depends(get_current_user)):
    if not payload.url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise HTTPException(status_code=400, detail="보안을 위해 AnkiConnect는 이 컴퓨터의 로컬 주소만 사용할 수 있습니다.")
    for key, value in payload.model_dump().items():
        db_set_meta(f"vocabulary:{current_user}:anki_{key}" if key != "obsidian_path" else f"vocabulary:{current_user}:obsidian_path", value)
    return {"ok": True}


@router.post("/vocabulary/review/start")
async def start_review(current_user: str = Depends(get_current_user)):
    await _ensure_anki_started(current_user)
    deck = get_anki_config(current_user)["deck"]
    try:
        await invoke("guiDeckReview", {"name": deck}, username=current_user)
        await asyncio.sleep(0.1)
        card = await _current_review_card(current_user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"complete": card is None, "card": card}


@router.post("/vocabulary/review/reveal")
async def reveal_review_card(current_user: str = Depends(get_current_user)):
    try:
        shown = await invoke("guiShowAnswer", username=current_user)
        card = await _current_review_card(current_user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"shown": bool(shown), "card": card}


@router.post("/vocabulary/review/answer")
async def answer_review_card(payload: ReviewAnswerRequest, current_user: str = Depends(get_current_user)):
    try:
        answered = await invoke("guiAnswerCard", {"ease": payload.ease}, username=current_user)
        if not answered:
            raise HTTPException(status_code=409, detail="먼저 카드의 답을 확인해 주세요.")
        await asyncio.sleep(0.12)
        card = await _current_review_card(current_user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"complete": card is None, "card": card}
