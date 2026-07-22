import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# DB_PATH 환경변수가 있으면 그대로 쓰고(Docker 등에서 데이터 볼륨 경로로
# 지정), 없으면 기존과 동일하게 backend/의 부모 기준 경로를 그대로 쓴다.
DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "easypaper.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """데이터베이스 테이블을 생성하고 기본 사용자를 설정합니다."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 1. users 테이블
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)
        
        # 2. documents 테이블
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            filename TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            total_pages INTEGER NOT NULL,
            metadata TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE ON UPDATE CASCADE
        )
        """)
        
        # 3. translations 테이블
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            suffix TEXT NOT NULL,
            translation TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE,
            UNIQUE(doc_id, page_num, suffix)
        )
        """)
        
        # 4. chats 테이블 (채팅 내역)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """)

        # 5. page_insights 테이블 (페이지별 키워드/단어 설명, 요약 캐시)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS page_insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            page_num INTEGER NOT NULL,
            kind TEXT NOT NULL,
            suffix TEXT NOT NULL,
            content TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE,
            UNIQUE(doc_id, page_num, kind, suffix)
        )
        """)

        # documents 테이블 동적 스키마 마이그레이션 (is_deleted 컬럼 추가)
        try:
            cursor.execute("ALTER TABLE documents ADD COLUMN is_deleted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # 6. app_meta 테이블 (자동 업데이트 확인 주기/마지막 확인 시각/마지막으로
        #    "업데이트 완료" 알림을 보여준 버전 등, 자주 바뀌는 앱 내부 상태를
        #    저장하는 범용 key-value 테이블)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        conn.commit()
        
    # 기본 관리자 계정 초기 생성
    from config import get_app_username, get_app_password_hash
    default_user = get_app_username()
    default_hash = get_app_password_hash()
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE username = ?", (default_user,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (default_user, default_hash, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            print(f"Default user '{default_user}' created in SQLite database.")


# ── 사용자 (Users) ───────────────────────────────────────────────────────────

def get_user(username: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username, password_hash, created_at FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

def create_user(username: str, password_hash: str) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False

def update_user_credentials(old_username: str, new_username: str, new_password_hash: str) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET username = ?, password_hash = ? WHERE username = ?",
                (new_username, new_password_hash, old_username)
            )
            # documents.username은 users.username을 참조하는 외래키로 선언돼
            # 있지만, SQLite는 연결마다 별도로 PRAGMA foreign_keys를 켜주지
            # 않는 한 이 외래키 제약(및 ON UPDATE CASCADE)을 전혀 강제하지
            # 않는다. 이 프로젝트는 그 PRAGMA를 켜지 않으므로, 아이디를
            # 바꾸면 documents 테이블은 예전 아이디를 그대로 가리킨 채 남아
            # 라이브러리 목록 조회(WHERE username = 새 아이디)에서 전부
            # 빠져버려 문서가 사라진 것처럼 보이는 문제가 있었다. 같은
            # 트랜잭션 안에서 명시적으로 함께 갱신한다.
            if new_username != old_username:
                cursor.execute(
                    "UPDATE documents SET username = ? WHERE username = ?",
                    (new_username, old_username)
                )
            conn.commit()
            return True
    except Exception:
        return False


# ── 문서 (Documents) ──────────────────────────────────────────────────────────

def db_save_document(doc_id: str, username: str, filename: str, pdf_path: str, total_pages: int, metadata: dict) -> dict:
    meta_str = json.dumps(metadata, ensure_ascii=False)
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO documents (id, username, filename, pdf_path, total_pages, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, username, filename, pdf_path, total_pages, meta_str, created_at)
        )
        conn.commit()
    return {
        "id": doc_id,
        "username": username,
        "filename": filename,
        "pdf_path": pdf_path,
        "total_pages": total_pages,
        "metadata": metadata,
        "created_at": created_at
    }

def db_get_document(doc_id: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, created_at FROM documents WHERE id = ?",
            (doc_id,)
        )
        row = cursor.fetchone()
        if row:
            doc = dict(row)
            doc["metadata"] = json.loads(doc["metadata"]) if doc["metadata"] else {}
            return doc
        return None

def db_list_documents(username: Optional[str] = None, only_trash: bool = False) -> list:
    is_deleted_val = 1 if only_trash else 0
    with get_db() as conn:
        cursor = conn.cursor()
        if username:
            cursor.execute(
                "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, created_at FROM documents WHERE username = ? AND is_deleted = ? ORDER BY created_at DESC",
                (username, is_deleted_val)
            )
        else:
            cursor.execute(
                "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, created_at FROM documents WHERE is_deleted = ? ORDER BY created_at DESC",
                (is_deleted_val,)
            )
        rows = cursor.fetchall()
        docs = []
        for r in rows:
            doc = dict(r)
            doc["metadata"] = json.loads(doc["metadata"]) if doc["metadata"] else {}
            docs.append(doc)
        return docs


def _escape_like(text: str) -> str:
    """LIKE 패턴의 와일드카드(%, _)를 리터럴로 취급하도록 이스케이프한다."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def db_search_documents(username: str, query: str, only_trash: bool = False) -> list:
    """파일명/제목·카테고리(metadata)와 페이지별 번역 텍스트를 가로질러 검색어를
    찾아 매칭되는 문서 목록을 반환합니다 (대소문자 구분 없음).

    원문(PDF에서 추출한 텍스트)은 세션 메모리에만 있고 DB에 영속화되지
    않아 검색 대상에 포함하지 못한다 - 파일명/제목/카테고리와 번역된
    텍스트만 검색 대상이다.
    """
    is_deleted_val = 1 if only_trash else 0
    like_query = f"%{_escape_like(query)}%"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT d.id, d.username, d.filename, d.pdf_path, d.total_pages,
                   d.metadata, d.is_deleted, d.created_at
            FROM documents d
            LEFT JOIN translations t ON t.doc_id = d.id
            WHERE d.username = ? AND d.is_deleted = ?
              AND (
                    d.filename LIKE ? ESCAPE '\\'
                 OR d.metadata LIKE ? ESCAPE '\\'
                 OR t.translation LIKE ? ESCAPE '\\'
              )
            ORDER BY d.created_at DESC
            """,
            (username, is_deleted_val, like_query, like_query, like_query)
        )
        rows = cursor.fetchall()
        docs = []
        for r in rows:
            doc = dict(r)
            doc["metadata"] = json.loads(doc["metadata"]) if doc["metadata"] else {}
            docs.append(doc)
        return docs


def db_delete_document(doc_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE id = ?", (doc_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
        return True

def db_soft_delete_document(doc_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE id = ?", (doc_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("UPDATE documents SET is_deleted = 1 WHERE id = ?", (doc_id,))
        conn.commit()
        return True

def db_restore_document(doc_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM documents WHERE id = ?", (doc_id,))
        if not cursor.fetchone():
            return False
        cursor.execute("UPDATE documents SET is_deleted = 0 WHERE id = ?", (doc_id,))
        conn.commit()
        return True


# ── 번역 (Translations) ────────────────────────────────────────────────────────

def db_save_translation(doc_id: str, page_num: int, translation: str, suffix: str = "") -> None:
    saved_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO translations (doc_id, page_num, suffix, translation, saved_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doc_id, page_num, suffix, translation, saved_at)
        )
        conn.commit()

def db_get_translation(doc_id: str, page_num: int, suffix: str = "", fallback: bool = True) -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        if suffix:
            cursor.execute(
                "SELECT translation FROM translations WHERE doc_id = ? AND page_num = ? AND suffix = ?",
                (doc_id, page_num, suffix)
            )
            row = cursor.fetchone()
            if row:
                return row["translation"]
        
        if fallback:
            # Fallback: get the most recently saved translation for this page, regardless of suffix
            cursor.execute(
                "SELECT translation FROM translations WHERE doc_id = ? AND page_num = ? ORDER BY saved_at DESC LIMIT 1",
                (doc_id, page_num)
            )
            row = cursor.fetchone()
            if row:
                return row["translation"]
        return None

def db_list_translated_pages(doc_id: str, suffix: str = "") -> List[int]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT page_num FROM translations WHERE doc_id = ? AND suffix = ? ORDER BY page_num ASC",
            (doc_id, suffix)
        )
        return [r["page_num"] for r in cursor.fetchall()]


# ── 채팅 (Chats) ──────────────────────────────────────────────────────────────

def db_save_chat_message(doc_id: str, role: str, content: str) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        # Safety check: avoid duplicating the exact last message
        cursor.execute(
            "SELECT role, content FROM chats WHERE doc_id = ? ORDER BY id DESC LIMIT 1",
            (doc_id,)
        )
        last_msg = cursor.fetchone()
        if last_msg and last_msg["role"] == role and last_msg["content"] == content:
            return
            
        cursor.execute(
            "INSERT INTO chats (doc_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (doc_id, role, content, created_at)
        )
        conn.commit()

def db_get_chat_history(doc_id: str) -> List[Dict[str, str]]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT role, content FROM chats WHERE doc_id = ? ORDER BY id ASC",
            (doc_id,)
        )
        return [{"role": r["role"], "content": r["content"]} for r in cursor.fetchall()]

def db_clear_chat_history(doc_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chats WHERE doc_id = ?", (doc_id,))
        conn.commit()

# ── 페이지 인사이트 (키워드/단어, 요약) ─────────────────────────────────────────

def db_save_page_insight(doc_id: str, page_num: int, kind: str, content: str, suffix: str = "") -> None:
    saved_at = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO page_insights (doc_id, page_num, kind, suffix, content, saved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doc_id, page_num, kind, suffix, content, saved_at)
        )
        conn.commit()

def db_get_page_insight(doc_id: str, page_num: int, kind: str, suffix: str = "") -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM page_insights WHERE doc_id = ? AND page_num = ? AND kind = ? AND suffix = ?",
            (doc_id, page_num, kind, suffix)
        )
        row = cursor.fetchone()
        return row["content"] if row else None

def db_clear_page_insights(doc_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM page_insights WHERE doc_id = ?", (doc_id,))
        conn.commit()


def db_clear_translations(doc_id: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM translations WHERE doc_id = ?", (doc_id,))
        conn.commit()

def db_update_document_metadata(doc_id: str, metadata: dict) -> None:
    meta_str = json.dumps(metadata, ensure_ascii=False)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            (meta_str, doc_id)
        )
        conn.commit()


# ── 앱 내부 상태 (app_meta) ───────────────────────────────────────────────────

def db_get_meta(key: str) -> Optional[str]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_meta WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None


def db_set_meta(key: str, value: str) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )
        conn.commit()
