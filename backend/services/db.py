import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# DB_PATH 환경변수가 있으면 그대로 쓰고(Docker 등에서 데이터 볼륨 경로로
# 지정), 없으면 기존과 동일하게 backend/의 부모 기준 경로를 그대로 쓴다.
DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "easypaper.db")

def get_db():
    # busy_timeout: 번역 잡이 쓰기 커넥션을 잡고 있는 동안(db_save_translation)
    # 라이브러리 목록 등 읽기 커넥션이 "database is locked"로 즉시 실패하지
    # 않고 잠깐 대기하도록 한다. journal_mode는 커넥션 단위가 아니라 DB
    # 파일에 영속되는 설정이라 init_db()에서 한 번만 WAL로 전환해두면 된다.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
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

        # 7. compare_sessions 테이블 (논문 비교 채팅 세션 id ↔ 비교 대상 문서
        #    id 목록 매핑). chats.doc_id에 쓰이는 "cmp_"+hash 값은 원본
        #    doc_ids 조합을 역산할 수 없는 단방향 해시라서, 라이브러리에서
        #    "이 비교 세션이 어떤 논문들의 비교인지"를 보여주려면 이 매핑을
        #    별도로 저장해둬야 한다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS compare_sessions (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            doc_ids TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # 라이브러리 목록/문서 조회가 doc_id(+suffix)로 translations를,
        # doc_id로 documents/page_insights/chats를 매번 훑는데(문서 수 x
        # 페이지 수만큼 뻥튀기됨) 인덱스가 없어 전부 풀스캔이었다. 목록
        # 화면을 열 때마다, 그리고 4초 폴링마다 이 비용을 반복해서 냈다.
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_translations_doc_suffix ON translations(doc_id, suffix, page_num)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_translations_doc_saved ON translations(doc_id, saved_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_page_insights_doc ON page_insights(doc_id, kind, suffix)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_username_deleted ON documents(username, is_deleted, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chats_doc ON chats(doc_id, id)")

        conn.commit()

        # WAL: 여러 읽기 커넥션이 번역 잡의 쓰기 커넥션과 부딪혀 잠기는
        # 문제를 줄인다. 커넥션 단위 PRAGMA가 아니라 DB 파일에 영속되는
        # 설정이라 여기서 한 번만 켜두면 이후 모든 커넥션에 적용된다.
        conn.execute("PRAGMA journal_mode=WAL")
        
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


def db_bulk_translation_rows(doc_ids: List[str]) -> Dict[str, List[tuple]]:
    """여러 문서의 (page_num, suffix, saved_at) 행을 한 번의 커넥션으로 모아
    doc_id별로 묶어 반환합니다.

    라이브러리 목록 화면은 문서마다 번역 완료 페이지 목록을 보여주는데,
    예전에는 문서 개수(N)만큼 매번 새 sqlite3 커넥션을 열어 최대 3개의
    쿼리를 던졌다(list_documents 참고). 문서가 늘어날수록, 그리고 4초
    폴링이 반복될수록 이 비용이 그대로 누적돼 라이브러리 화면 자체가
    느려지는 원인이었다. IN절 변수 개수 제한(SQLITE_MAX_VARIABLE_NUMBER,
    보통 999)을 넘지 않도록 청크 단위로 나눠 조회한다.
    """
    result: Dict[str, list] = {doc_id: [] for doc_id in doc_ids}
    if not doc_ids:
        return result

    CHUNK = 500
    with get_db() as conn:
        cursor = conn.cursor()
        for i in range(0, len(doc_ids), CHUNK):
            chunk = doc_ids[i:i + CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT doc_id, page_num, suffix, saved_at FROM translations WHERE doc_id IN ({placeholders})",
                chunk
            )
            for row in cursor.fetchall():
                result[row["doc_id"]].append((row["page_num"], row["suffix"], row["saved_at"]))
    return result


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


def db_list_assistant_chat_sessions(username: str) -> List[Dict[str, Any]]:
    """사용자가 AI 어시스턴트(단일 논문) 기능으로 대화한 채팅 세션 목록을,
    최근 대화 시각 역순으로 반환합니다. 비교 채팅(doc_id가 "cmp_"로 시작)은
    제외하고, 휴지통에 있거나 삭제된 문서의 대화도 제외합니다."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.id AS doc_id, d.filename, d.metadata, MAX(c.created_at) AS last_message_at
            FROM chats c
            JOIN documents d ON d.id = c.doc_id
            WHERE d.username = ? AND d.is_deleted = 0 AND c.doc_id NOT LIKE 'cmp\\_%' ESCAPE '\\'
            GROUP BY c.doc_id
            ORDER BY last_message_at DESC
            """,
            (username,)
        )
        sessions = []
        for r in cursor.fetchall():
            row = dict(r)
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            title = metadata.get("title") or row["filename"]
            sessions.append({
                "doc_id": row["doc_id"],
                "title": title,
                "last_message_at": row["last_message_at"],
            })
        return sessions


def db_upsert_compare_session(compare_id: str, username: str, doc_ids: List[str]) -> None:
    """비교 채팅 세션 id와 그 대상 문서 id 목록의 매핑을 저장/갱신합니다."""
    now = datetime.now(timezone.utc).isoformat()
    doc_ids_json = json.dumps(doc_ids, ensure_ascii=False)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO compare_sessions (id, username, doc_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (compare_id, username, doc_ids_json, now, now)
        )
        conn.commit()


def db_list_compare_chat_sessions(username: str) -> List[Dict[str, Any]]:
    """사용자가 논문 비교 기능으로 대화한 채팅 세션 목록을, 최근 대화 시각
    역순으로 반환합니다. 각 세션의 doc_ids에 연결된 문서 제목도 함께 담습니다."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, doc_ids, updated_at FROM compare_sessions WHERE username = ? ORDER BY updated_at DESC",
            (username,)
        )
        rows = cursor.fetchall()

        sessions = []
        for r in rows:
            row = dict(r)
            doc_ids = json.loads(row["doc_ids"])

            titles = []
            for doc_id in doc_ids:
                cursor.execute(
                    "SELECT filename, metadata FROM documents WHERE id = ? AND username = ?",
                    (doc_id, username)
                )
                doc_row = cursor.fetchone()
                if not doc_row:
                    titles.append("(삭제된 논문)")
                    continue
                doc = dict(doc_row)
                metadata = json.loads(doc["metadata"]) if doc["metadata"] else {}
                titles.append(metadata.get("title") or doc["filename"])

            sessions.append({
                "id": row["id"],
                "doc_ids": doc_ids,
                "titles": titles,
                "last_message_at": row["updated_at"],
            })
        return sessions


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
