import os
import shutil
import json
from typing import Optional, List
from config import LIBRARY_DIR
from services.db import (
    db_save_document,
    db_get_document,
    db_list_documents,
    db_delete_document,
    db_soft_delete_document,
    db_restore_document,
    db_save_translation,
    db_get_translation,
    db_clear_translations,
    db_save_page_insight,
    db_get_page_insight,
    db_clear_page_insights,
    db_update_document_metadata,
    get_db
)

def _pdf_path(doc_id: str) -> str:
    return os.path.join(LIBRARY_DIR, doc_id, "document.pdf")

def _cover_path(doc_id: str) -> str:
    return os.path.join(LIBRARY_DIR, doc_id, "cover.jpg")


# ── 문서 저장 ─────────────────────────────────────────────────────────────────

def save_document(doc_id: str, filename: str, pdf_src_path: str,
                  total_pages: int, metadata: dict, username: str = "admin") -> dict:
    """PDF를 라이브러리에 영구 저장하고 데이터베이스에 기록합니다."""
    doc_dir = os.path.join(LIBRARY_DIR, doc_id)
    os.makedirs(os.path.join(doc_dir, "translations"), exist_ok=True)

    # PDF 파일 복사
    shutil.copy2(pdf_src_path, _pdf_path(doc_id))

    # 데이터베이스에 저장
    doc_meta = db_save_document(doc_id, username, filename, _pdf_path(doc_id), total_pages, metadata)
    doc_meta["translated_pages"] = []
    return doc_meta


def get_document(
    doc_id: str,
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None
) -> Optional[dict]:
    """라이브러리에서 문서 메타데이터를 가져옵니다."""
    doc = db_get_document(doc_id)
    if doc:
        suffix = None
        if target_lang is not None and style is not None:
            suffix = f"{target_lang}_{style}_math{int(ignore_math)}_table{int(ignore_table)}_refs{int(ignore_refs)}"
            
        with get_db() as conn:
            cursor = conn.cursor()
            pages = []
            if suffix:
                cursor.execute(
                    "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? AND suffix = ? ORDER BY page_num ASC",
                    (doc_id, suffix)
                )
                pages = [r["page_num"] for r in cursor.fetchall()]
            
            if not pages:
                # Fallback to the most recent suffix's pages
                cursor.execute(
                    "SELECT suffix FROM translations WHERE doc_id = ? ORDER BY saved_at DESC LIMIT 1",
                    (doc_id,)
                )
                row = cursor.fetchone()
                if row:
                    fallback_suffix = row["suffix"]
                    cursor.execute(
                        "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? AND suffix = ? ORDER BY page_num ASC",
                        (doc_id, fallback_suffix)
                    )
                    pages = [r["page_num"] for r in cursor.fetchall()]
                else:
                    # If no suffix found, query any pages
                    cursor.execute(
                        "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? ORDER BY page_num ASC",
                        (doc_id,)
                    )
                    pages = [r["page_num"] for r in cursor.fetchall()]
        doc["translated_pages"] = pages
        return doc
    return None


def list_documents(
    username: Optional[str] = None,
    target_lang: Optional[str] = None,
    style: Optional[str] = None,
    ignore_math: Optional[bool] = None,
    ignore_table: Optional[bool] = None,
    ignore_refs: Optional[bool] = None,
    only_trash: bool = False
) -> list:
    """라이브러리의 문서를 최신순으로 반환합니다 (필터링 가능)."""
    docs = db_list_documents(username, only_trash=only_trash)
    
    suffix = None
    if target_lang is not None and style is not None:
        suffix = f"{target_lang}_{style}_math{int(ignore_math)}_table{int(ignore_table)}_refs{int(ignore_refs)}"

    for doc in docs:
        with get_db() as conn:
            cursor = conn.cursor()
            pages = []
            if suffix:
                cursor.execute(
                    "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? AND suffix = ? ORDER BY page_num ASC",
                    (doc["id"], suffix)
                )
                pages = [r["page_num"] for r in cursor.fetchall()]
            
            if not pages:
                # Fallback to the most recent suffix's pages
                cursor.execute(
                    "SELECT suffix FROM translations WHERE doc_id = ? ORDER BY saved_at DESC LIMIT 1",
                    (doc["id"],)
                )
                row = cursor.fetchone()
                if row:
                    fallback_suffix = row["suffix"]
                    cursor.execute(
                        "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? AND suffix = ? ORDER BY page_num ASC",
                        (doc["id"], fallback_suffix)
                    )
                    pages = [r["page_num"] for r in cursor.fetchall()]
                else:
                    # If no suffix found, query any pages
                    cursor.execute(
                        "SELECT DISTINCT page_num FROM translations WHERE doc_id = ? ORDER BY page_num ASC",
                        (doc["id"],)
                    )
                    pages = [r["page_num"] for r in cursor.fetchall()]
        doc["translated_pages"] = pages
    return docs


def delete_chat_sessions(doc_id: str) -> None:
    """논문 삭제 시 연동된 Claude Code 및 Antigravity 채팅 세션을 삭제합니다."""
    # 1. Claude Code 세션 캐시 디렉터리 삭제
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
    claude_home = os.path.join(cache_dir, f"claude_home_{doc_id}")
    if os.path.exists(claude_home):
        try:
            shutil.rmtree(claude_home, ignore_errors=True)
            print(f"[delete_chat_sessions] Deleted Claude Code session directory: {claude_home}")
        except Exception as e:
            print(f"[delete_chat_sessions Claude Code Error] {e}")

    # 2. Antigravity 세션 삭제 (신규 ai_session.json 우선, 예전 conversation_id.txt는 폴백)
    conv_id = None
    meta_path = os.path.join(LIBRARY_DIR, doc_id, "ai_session.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("provider") == "antigravity":
                conv_id = meta.get("conversation_id")
        except Exception as e:
            print(f"[delete_chat_sessions Antigravity Error] {e}")
    if not conv_id:
        conv_txt_path = os.path.join(LIBRARY_DIR, doc_id, "conversation_id.txt")
        if os.path.exists(conv_txt_path):
            try:
                with open(conv_txt_path, "r", encoding="utf-8") as f:
                    conv_id = f.read().strip()
            except Exception as e:
                print(f"[delete_chat_sessions Antigravity Error] {e}")
    if conv_id:
        try:
            # conversations/<conv_id>.db 파일 삭제
            db_path = os.path.expanduser(f"~/.gemini/antigravity-cli/conversations/{conv_id}.db")
            if os.path.exists(db_path):
                os.remove(db_path)
                print(f"[delete_chat_sessions] Deleted Antigravity conversation db: {db_path}")
            # brain/<conv_id>/ 디렉터리 삭제
            brain_dir = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{conv_id}")
            if os.path.exists(brain_dir):
                shutil.rmtree(brain_dir, ignore_errors=True)
                print(f"[delete_chat_sessions] Deleted Antigravity brain directory: {brain_dir}")
        except Exception as e:
            print(f"[delete_chat_sessions Antigravity Error] {e}")


def permanently_delete_document(doc_id: str) -> bool:
    """라이브러리에서 문서 파일 및 데이터베이스 레코드를 영구히 삭제합니다."""
    doc_dir = os.path.join(LIBRARY_DIR, doc_id)
    # 1. 채팅 세션 삭제
    delete_chat_sessions(doc_id)
    # 2. 파일 삭제
    if os.path.exists(doc_dir):
        shutil.rmtree(doc_dir, ignore_errors=True)
    # 3. DB 삭제
    return db_delete_document(doc_id)

def soft_delete_document(doc_id: str) -> bool:
    """라이브러리 문서를 휴지통으로 이동(Soft Delete)시킵니다."""
    return db_soft_delete_document(doc_id)

def restore_document(doc_id: str) -> bool:
    """휴지통의 문서를 복원합니다."""
    return db_restore_document(doc_id)

def empty_trash(username: str = "admin") -> bool:
    """휴지통에 있는 모든 문서를 영구 삭제(하드 딜리트)합니다."""
    trashed_docs = db_list_documents(username, only_trash=True)
    for doc in trashed_docs:
        permanently_delete_document(doc["id"])
    return True


# ── 번역 저장/조회 ─────────────────────────────────────────────────────────────

def save_translation(doc_id: str, page_num: int, translation: str, suffix: str = "") -> None:
    """번역 결과를 데이터베이스에 저장합니다."""
    db_save_translation(doc_id, page_num, translation, suffix)


def get_translation(doc_id: str, page_num: int, suffix: str = "", fallback: bool = True) -> Optional[str]:
    """데이터베이스에서 번역 결과를 가져옵니다."""
    val = db_get_translation(doc_id, page_num, suffix, fallback)
    if val and val.startswith("{"):
        try:
            data = json.loads(val)
            if isinstance(data, dict) and "translation" in data:
                return data["translation"]
        except Exception:
            pass
    return val


def get_translation_full(doc_id: str, page_num: int, suffix: str = "", fallback: bool = True) -> dict:
    """데이터베이스에서 번역 결과와 문장 매핑 데이터를 통째로 가져옵니다."""
    val = db_get_translation(doc_id, page_num, suffix, fallback)
    if val and val.startswith("{"):
        try:
            data = json.loads(val)
            if isinstance(data, dict) and "translation" in data:
                return data
        except Exception:
            pass
    return {"translation": val or "", "sentences": []}


def clear_translations(doc_id: str) -> None:
    """데이터베이스에서 모든 번역 데이터를 지웁니다."""
    db_clear_translations(doc_id)


# ── 페이지 인사이트 (키워드/단어, 요약) ─────────────────────────────────────────

def save_page_insight(doc_id: str, page_num: int, kind: str, content: str, suffix: str = "") -> None:
    """키워드/단어 설명 또는 요약 결과를 데이터베이스에 저장합니다."""
    db_save_page_insight(doc_id, page_num, kind, content, suffix)


def get_page_insight(doc_id: str, page_num: int, kind: str, suffix: str = "") -> Optional[str]:
    """데이터베이스에서 키워드/단어 설명 또는 요약 결과를 가져옵니다."""
    return db_get_page_insight(doc_id, page_num, kind, suffix)


def clear_page_insights(doc_id: str) -> None:
    """데이터베이스에서 모든 페이지 인사이트(키워드/요약) 데이터를 지웁니다."""
    db_clear_page_insights(doc_id)


def get_pdf_path(doc_id: str) -> Optional[str]:
    """라이브러리 PDF 파일 경로를 반환합니다."""
    path = _pdf_path(doc_id)
    return path if os.path.exists(path) else None

def get_cover_path(doc_id: str) -> Optional[str]:
    """1페이지 상단(제목+abstract) 미리보기 이미지 경로를 반환합니다. 아직 생성되지
    않았다면 이 시점에 만들어 캐시합니다(문서당 최초 1회만 렌더링 비용 발생)."""
    pdf_path = get_pdf_path(doc_id)
    if not pdf_path:
        return None
    cover_path = _cover_path(doc_id)
    if not os.path.exists(cover_path):
        from services.pdf_parser import render_cover_image
        try:
            if not render_cover_image(pdf_path, cover_path):
                return None
        except Exception as e:
            print(f"[get_cover_path] 표지 이미지 생성 실패 ({doc_id}): {e}")
            return None
    return cover_path

def update_document_metadata(doc_id: str, metadata: dict) -> None:
    """문서 메타데이터를 업데이트합니다."""
    db_update_document_metadata(doc_id, metadata)
