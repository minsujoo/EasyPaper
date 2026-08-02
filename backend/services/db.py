import sqlite3
import os
import json
from datetime import datetime, timedelta, timezone
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

        # 라이브러리 폴더. 기존 documents 행은 folder_id=NULL인 "미분류"로
        # 자연스럽게 마이그레이션되어 사용자의 기존 보관함 구성이 유지된다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS library_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(username, name)
        )
        """)
        try:
            cursor.execute("ALTER TABLE documents ADD COLUMN folder_id INTEGER DEFAULT NULL")
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

        # 8. paper_notes 테이블 (업로드 직후 자동 생성되는 논문 정리 노트).
        # page_insights와 달리 생성 상태/실패 원인을 홈 대시보드에서 바로
        # 보여줘야 하므로 독립 테이블로 관리한다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_notes (
            doc_id TEXT PRIMARY KEY,
            language TEXT NOT NULL,
            status TEXT NOT NULL,
            content TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """)

        # Scholar 추천 피드의 명시적 관심 신호. paper_json을 함께 보관해
        # 긍정 평가한 외부 논문도 다음 추천의 관심사 문맥으로 재사용한다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_feedback (
            username TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            paper_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, paper_id)
        )
        """)

        # 추천 노출·열람·숨김 기록. 피드를 새로 열 때마다 같은 논문이 반복되는
        # 문제를 막고, 마지막 방문 이후 Catch-up 범위를 계산하는 기반이다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_impressions (
            username TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            paper_json TEXT NOT NULL,
            feed_mode TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1,
            opened_at TEXT,
            hidden_at TEXT,
            PRIMARY KEY (username, paper_id)
        )
        """)

        # 공개 PDF 유무와 관계없이 외부 학술 레코드를 폴더에 보관한다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_bookmarks (
            username TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            paper_json TEXT NOT NULL,
            folder_id INTEGER,
            imported_doc_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, paper_id)
        )
        """)
        try:
            cursor.execute("ALTER TABLE scholar_bookmarks ADD COLUMN imported_doc_id TEXT DEFAULT NULL")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_feed_state (
            username TEXT PRIMARY KEY,
            last_visit_at TEXT,
            last_feed_at TEXT,
            last_crawl_at TEXT,
            crawl_interval_hours INTEGER NOT NULL DEFAULT 24
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_digest_cache (
            username TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            paper_json TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            publication_date TEXT,
            PRIMARY KEY (username, paper_id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scholar_conference_watch (
            username TEXT NOT NULL,
            conference_id TEXT NOT NULL,
            conference_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (username, conference_id)
        )
        """)

        # 날짜별 논문 열람 기록. documents.metadata의 last_page는 문서별 마지막
        # 위치만 알 수 있어 달력형 히스토리를 만들 수 없으므로, 날짜 단위 활동을
        # 별도로 누적한다. activity_date는 사용자의 로컬 날짜를 클라이언트가
        # 전달해 자정 전후에도 달력 날짜가 어긋나지 않게 한다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reading_activity (
            username TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            activity_date TEXT NOT NULL,
            first_opened_at TEXT NOT NULL,
            last_read_at TEXT NOT NULL,
            last_page INTEGER NOT NULL,
            furthest_page INTEGER NOT NULL,
            total_pages INTEGER NOT NULL,
            PRIMARY KEY (username, doc_id, activity_date),
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE
        )
        """)

        # 논문을 읽다가 수집한 영어 단어/구문 카드. Anki가 실행 중이 아니어도
        # 먼저 로컬에 저장한 뒤 나중에 다시 동기화할 수 있도록 상태를 함께 둔다.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vocabulary_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            page_num INTEGER,
            term TEXT NOT NULL,
            normalized_term TEXT NOT NULL,
            meaning_ko TEXT NOT NULL,
            context_en TEXT NOT NULL,
            context_ko TEXT NOT NULL,
            paper_title TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER,
            anki_status TEXT NOT NULL DEFAULT 'pending',
            anki_error TEXT,
            obsidian_synced INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents (id) ON DELETE CASCADE,
            UNIQUE(username, doc_id, normalized_term)
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_paper_notes_status ON paper_notes(status, updated_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_library_folders_username ON library_folders(username, name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_folder ON documents(username, folder_id, is_deleted)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_feedback_user_rating ON scholar_feedback(username, rating, updated_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_impressions_user_seen ON scholar_impressions(username, last_seen_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_impressions_user_hidden ON scholar_impressions(username, hidden_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_bookmarks_user_folder ON scholar_bookmarks(username, folder_id, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_digest_user_date ON scholar_digest_cache(username, discovered_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scholar_conference_watch_user ON scholar_conference_watch(username, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reading_activity_user_date ON reading_activity(username, activity_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vocabulary_user_updated ON vocabulary_cards(username, updated_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vocabulary_anki_status ON vocabulary_cards(username, anki_status)")

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
                cursor.execute(
                    "UPDATE reading_activity SET username = ? WHERE username = ?",
                    (new_username, old_username)
                )
                for table in (
                    "scholar_feedback", "scholar_impressions", "scholar_bookmarks",
                    "scholar_feed_state", "scholar_digest_cache", "scholar_conference_watch",
                    "vocabulary_cards",
                ):
                    cursor.execute(
                        f"UPDATE {table} SET username = ? WHERE username = ?",
                        (new_username, old_username),
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
            "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, folder_id, created_at FROM documents WHERE id = ?",
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
                "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, folder_id, created_at FROM documents WHERE username = ? AND is_deleted = ? ORDER BY created_at DESC",
                (username, is_deleted_val)
            )
        else:
            cursor.execute(
                "SELECT id, username, filename, pdf_path, total_pages, metadata, is_deleted, folder_id, created_at FROM documents WHERE is_deleted = ? ORDER BY created_at DESC",
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
                   d.metadata, d.is_deleted, d.folder_id, d.created_at
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
        # SQLite foreign_keys가 커넥션마다 활성화되지 않는 현재 구조에서는
        # ON DELETE CASCADE만 믿으면 노트가 고아 레코드로 남는다.
        cursor.execute("DELETE FROM paper_notes WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM reading_activity WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
        return True


# ── 독서 활동 (Reading Activity) ─────────────────────────────────────────────

def db_record_reading_activity(
    username: str,
    doc_id: str,
    activity_date: str,
    page: int,
    total_pages: int,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    page = max(1, int(page))
    total_pages = max(page, int(total_pages or page))
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO reading_activity
                (username, doc_id, activity_date, first_opened_at, last_read_at,
                 last_page, furthest_page, total_pages)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username, doc_id, activity_date) DO UPDATE SET
                last_read_at = excluded.last_read_at,
                last_page = excluded.last_page,
                furthest_page = MAX(reading_activity.furthest_page, excluded.furthest_page),
                total_pages = excluded.total_pages
            """,
            (username, doc_id, activity_date, now, now, page, page, total_pages),
        )
        conn.commit()
    return {
        "doc_id": doc_id,
        "activity_date": activity_date,
        "last_read_at": now,
        "last_page": page,
        "furthest_page": page,
        "total_pages": total_pages,
    }


def db_list_reading_activity(username: str, year: int, month: int) -> List[dict]:
    month_prefix = f"{year:04d}-{month:02d}"
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.doc_id, a.activity_date, a.first_opened_at, a.last_read_at,
                   a.last_page, a.furthest_page, a.total_pages,
                   d.filename, d.metadata, d.folder_id
            FROM reading_activity a
            JOIN documents d ON d.id = a.doc_id
            WHERE a.username = ? AND a.activity_date LIKE ? AND d.is_deleted = 0
            ORDER BY a.activity_date, a.last_read_at DESC
            """,
            (username, month_prefix + "%"),
        )
        activities = []
        for row in cursor.fetchall():
            item = dict(row)
            try:
                item["metadata"] = json.loads(item["metadata"]) if item["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                item["metadata"] = {}
            activities.append(item)
        return activities

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


# ── 라이브러리 폴더 (Library folders) ────────────────────────────────────────

def db_list_folders(username: str) -> List[Dict[str, Any]]:
    """사용자 폴더와 현재 보관함(휴지통 제외)의 논문 수를 반환한다."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.created_at, COUNT(d.id) AS document_count
            FROM library_folders f
            LEFT JOIN documents d
              ON d.folder_id = f.id AND d.username = f.username AND d.is_deleted = 0
            WHERE f.username = ?
            GROUP BY f.id, f.name, f.created_at
            ORDER BY f.name COLLATE NOCASE, f.id
            """,
            (username,),
        ).fetchall()
        return [dict(row) for row in rows]


def db_create_folder(username: str, name: str) -> Optional[Dict[str, Any]]:
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO library_folders (username, name, created_at) VALUES (?, ?, ?)",
                (username, name, created_at),
            )
            conn.commit()
            return {"id": cursor.lastrowid, "name": name, "created_at": created_at, "document_count": 0}
    except sqlite3.IntegrityError:
        return None


def db_get_folder(folder_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, username, name, created_at FROM library_folders WHERE id = ?",
            (folder_id,),
        ).fetchone()
        return dict(row) if row else None


def db_rename_folder(folder_id: int, username: str, name: str) -> bool:
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "UPDATE library_folders SET name = ? WHERE id = ? AND username = ?",
                (name, folder_id, username),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.IntegrityError:
        return False


def db_delete_folder(folder_id: int, username: str) -> bool:
    """폴더만 삭제하고 안의 논문은 미분류로 되돌린다."""
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM library_folders WHERE id = ? AND username = ?",
            (folder_id, username),
        ).fetchone()
        if not exists:
            return False
        conn.execute(
            "UPDATE documents SET folder_id = NULL WHERE folder_id = ? AND username = ?",
            (folder_id, username),
        )
        conn.execute(
            "DELETE FROM library_folders WHERE id = ? AND username = ?",
            (folder_id, username),
        )
        conn.commit()
        return True


def db_move_document_to_folder(doc_id: str, username: str, folder_id: Optional[int]) -> bool:
    with get_db() as conn:
        if folder_id is not None:
            folder = conn.execute(
                "SELECT 1 FROM library_folders WHERE id = ? AND username = ?",
                (folder_id, username),
            ).fetchone()
            if not folder:
                return False
        cursor = conn.execute(
            "UPDATE documents SET folder_id = ? WHERE id = ? AND username = ?",
            (folder_id, doc_id, username),
        )
        conn.commit()
        return cursor.rowcount > 0


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

def db_delete_page_insight(doc_id: str, page_num: int, kind: str, suffix: str = "") -> None:
    """특정 kind/suffix 하나만 지운다. db_clear_page_insights는 문서의 모든
    인사이트(키워드/요약/브리핑 등, 언어별로도 전부)를 지워버려 "이 브리핑
    하나만 다시 생성" 같은 용도로 쓰기엔 범위가 너무 넓다."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM page_insights WHERE doc_id = ? AND page_num = ? AND kind = ? AND suffix = ?",
            (doc_id, page_num, kind, suffix)
        )
        conn.commit()


# ── 논문 노트 (Paper Notes) ───────────────────────────────────────────────────

def db_set_paper_note_status(
    doc_id: str,
    language: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    """노트 생성 상태를 갱신한다. 재생성 중에는 기존 content를 유지해, 생성이
    끝나기 전에도 사용자가 이전 노트를 계속 열어볼 수 있게 한다."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_notes
                (doc_id, language, status, content, error, created_at, updated_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                language = excluded.language,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (doc_id, language, status, error, now, now),
        )
        conn.commit()


def db_save_paper_note(doc_id: str, language: str, content: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content_json = json.dumps(content, ensure_ascii=False)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_notes
                (doc_id, language, status, content, error, created_at, updated_at)
            VALUES (?, ?, 'ready', ?, NULL, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                language = excluded.language,
                status = 'ready',
                content = excluded.content,
                error = NULL,
                updated_at = excluded.updated_at
            """,
            (doc_id, language, content_json, now, now),
        )
        conn.commit()


def _decode_paper_note_row(row) -> Optional[dict]:
    if not row:
        return None
    note = dict(row)
    raw_content = note.get("content")
    if raw_content:
        try:
            note["content"] = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            note["content"] = None
    else:
        note["content"] = None
    return note


def db_get_paper_note(doc_id: str) -> Optional[dict]:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT doc_id, language, status, content, error, created_at, updated_at
            FROM paper_notes WHERE doc_id = ?
            """,
            (doc_id,),
        )
        return _decode_paper_note_row(cursor.fetchone())


def db_list_paper_notes(username: str) -> List[dict]:
    """홈 노트 탭용 목록. 아직 노트가 생성되지 않은 기존 문서도
    status='not_started'로 포함해 사용자가 수동 생성할 수 있게 한다."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.id AS doc_id, d.filename, d.metadata, d.total_pages,
                   d.created_at AS document_created_at,
                   n.language, n.status, n.content, n.error,
                   n.created_at, n.updated_at
            FROM documents d
            LEFT JOIN paper_notes n ON n.doc_id = d.id
            WHERE d.username = ? AND d.is_deleted = 0
            ORDER BY COALESCE(n.updated_at, d.created_at) DESC
            """,
            (username,),
        )
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            try:
                item["metadata"] = json.loads(item["metadata"]) if item["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                item["metadata"] = {}
            raw_content = item.get("content")
            if raw_content:
                try:
                    item["content"] = json.loads(raw_content)
                except (json.JSONDecodeError, TypeError):
                    item["content"] = None
            else:
                item["content"] = None
            if not item.get("status"):
                item["status"] = "not_started"
            results.append(item)
        return results


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


# ── Scholar 추천 피드 평가 ───────────────────────────────────────────────────

def db_set_scholar_feedback(username: str, paper_id: str, rating: int, paper: dict) -> dict:
    """외부 논문에 대한 관심(1)/비관심(-1)을 저장하고 같은 값을 누르면 해제한다."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        current = conn.execute(
            "SELECT rating FROM scholar_feedback WHERE username = ? AND paper_id = ?",
            (username, paper_id),
        ).fetchone()
        if current and int(current["rating"]) == rating:
            conn.execute(
                "DELETE FROM scholar_feedback WHERE username = ? AND paper_id = ?",
                (username, paper_id),
            )
            conn.commit()
            return {"paper_id": paper_id, "rating": 0}
        conn.execute(
            """
            INSERT INTO scholar_feedback (username, paper_id, rating, paper_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username, paper_id) DO UPDATE SET
              rating = excluded.rating,
              paper_json = excluded.paper_json,
              updated_at = excluded.updated_at
            """,
            (username, paper_id, rating, json.dumps(paper, ensure_ascii=False), now),
        )
        conn.commit()
    return {"paper_id": paper_id, "rating": rating}


def db_list_scholar_feedback(username: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT paper_id, rating, paper_json, updated_at
            FROM scholar_feedback WHERE username = ? ORDER BY updated_at DESC
            """,
            (username,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            paper = json.loads(row["paper_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            paper = {}
        result.append({
            "paper_id": row["paper_id"], "rating": int(row["rating"]),
            "paper": paper, "updated_at": row["updated_at"],
        })
    return result


def db_record_scholar_impressions(
    username: str, papers: list[dict], feed_mode: str
) -> None:
    if not papers:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO scholar_impressions
                (username, paper_id, paper_json, feed_mode, first_seen_at, last_seen_at, seen_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(username, paper_id) DO UPDATE SET
                paper_json = excluded.paper_json,
                feed_mode = excluded.feed_mode,
                last_seen_at = excluded.last_seen_at,
                seen_count = scholar_impressions.seen_count + 1
            """,
            [
                (
                    username, str(paper.get("id") or ""),
                    json.dumps(paper, ensure_ascii=False), feed_mode, now, now,
                )
                for paper in papers if paper.get("id")
            ],
        )
        conn.commit()


def db_list_scholar_impressions(username: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT paper_id, paper_json, feed_mode, first_seen_at, last_seen_at,
                   seen_count, opened_at, hidden_at
            FROM scholar_impressions WHERE username = ?
            ORDER BY last_seen_at DESC
            """,
            (username,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["paper"] = json.loads(item.pop("paper_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["paper"] = {}
        result.append(item)
    return result


def db_set_scholar_interaction(username: str, paper_id: str, action: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    if action not in ("open", "hide", "unhide"):
        raise ValueError("unsupported scholar interaction")
    with get_db() as conn:
        row = conn.execute(
            "SELECT paper_id FROM scholar_impressions WHERE username = ? AND paper_id = ?",
            (username, paper_id),
        ).fetchone()
        if not row:
            return {"paper_id": paper_id, "updated": False}
        if action == "open":
            conn.execute(
                "UPDATE scholar_impressions SET opened_at = ? WHERE username = ? AND paper_id = ?",
                (now, username, paper_id),
            )
        else:
            conn.execute(
                "UPDATE scholar_impressions SET hidden_at = ? WHERE username = ? AND paper_id = ?",
                (now if action == "hide" else None, username, paper_id),
            )
        conn.commit()
    return {"paper_id": paper_id, "updated": True, "action": action}


def db_set_scholar_bookmark(
    username: str, paper_id: str, paper: dict, folder_id: Optional[int], saved: bool
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        if not saved:
            conn.execute(
                "DELETE FROM scholar_bookmarks WHERE username = ? AND paper_id = ?",
                (username, paper_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO scholar_bookmarks
                    (username, paper_id, paper_json, folder_id, imported_doc_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(username, paper_id) DO UPDATE SET
                    paper_json = excluded.paper_json,
                    folder_id = excluded.folder_id,
                    updated_at = excluded.updated_at
                """,
                (username, paper_id, json.dumps(paper, ensure_ascii=False), folder_id, now, now),
            )
        conn.commit()
    return {"paper_id": paper_id, "saved": saved, "folder_id": folder_id}


def db_list_scholar_bookmarks(
    username: str, folder_id: Optional[int] = None
) -> list[dict]:
    query = """
        SELECT paper_id, paper_json, folder_id, imported_doc_id, created_at, updated_at
        FROM scholar_bookmarks WHERE username = ?
    """
    params: list[Any] = [username]
    if folder_id is not None:
        query += " AND folder_id = ?"
        params.append(folder_id)
    query += " ORDER BY updated_at DESC"
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    result = []
    for row in rows:
        try:
            paper = json.loads(row["paper_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            paper = {}
        paper["bookmarked"] = True
        paper["bookmark_folder_id"] = row["folder_id"]
        paper["downloaded"] = bool(row["imported_doc_id"])
        paper["saved_document_id"] = row["imported_doc_id"]
        result.append({
            "paper_id": row["paper_id"], "paper": paper,
            "folder_id": row["folder_id"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "imported_doc_id": row["imported_doc_id"],
        })
    return result


def db_link_scholar_bookmark(username: str, paper_id: str, doc_id: str) -> None:
    """다운로드가 끝난 외부 레코드와 라이브러리 문서를 하나의 상태로 묶는다."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE scholar_bookmarks SET imported_doc_id = ?, updated_at = ?
            WHERE username = ? AND paper_id = ?
            """,
            (doc_id, datetime.now(timezone.utc).isoformat(), username, paper_id),
        )
        conn.commit()


def db_set_scholar_conference_watch(
    username: str, conference_id: str, conference: dict, watched: bool
) -> dict:
    with get_db() as conn:
        if watched:
            conn.execute(
                """
                INSERT INTO scholar_conference_watch
                    (username, conference_id, conference_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(username, conference_id) DO UPDATE SET
                    conference_json = excluded.conference_json
                """,
                (username, conference_id, json.dumps(conference, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
        else:
            conn.execute(
                "DELETE FROM scholar_conference_watch WHERE username = ? AND conference_id = ?",
                (username, conference_id),
            )
        conn.commit()
    return {"conference_id": conference_id, "watched": watched}


def db_list_scholar_conference_watch(username: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT conference_id, conference_json, created_at
            FROM scholar_conference_watch WHERE username = ? ORDER BY created_at DESC
            """,
            (username,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            conference = json.loads(row["conference_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            conference = {}
        result.append({
            "conference_id": row["conference_id"], "conference": conference,
            "created_at": row["created_at"],
        })
    return result


def db_get_scholar_feed_state(username: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM scholar_feed_state WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else {
        "username": username, "last_visit_at": None, "last_feed_at": None,
        "last_crawl_at": None, "crawl_interval_hours": 24,
    }


def db_touch_scholar_feed(
    username: str, *, visit: bool = True, crawl: bool = False
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO scholar_feed_state
                (username, last_visit_at, last_feed_at, last_crawl_at, crawl_interval_hours)
            VALUES (?, ?, ?, ?, 24)
            ON CONFLICT(username) DO UPDATE SET
                last_visit_at = CASE WHEN ? THEN excluded.last_visit_at ELSE scholar_feed_state.last_visit_at END,
                last_feed_at = excluded.last_feed_at,
                last_crawl_at = CASE WHEN ? THEN excluded.last_crawl_at ELSE scholar_feed_state.last_crawl_at END
            """,
            (username, now if visit else None, now, now if crawl else None, int(visit), int(crawl)),
        )
        conn.commit()
    return db_get_scholar_feed_state(username)


def db_save_scholar_digest_cache(username: str, papers: list[dict]) -> None:
    if not papers:
        return
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.executemany(
            """
            INSERT INTO scholar_digest_cache
                (username, paper_id, paper_json, discovered_at, publication_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username, paper_id) DO UPDATE SET
                paper_json = excluded.paper_json,
                discovered_at = excluded.discovered_at,
                publication_date = excluded.publication_date
            """,
            [
                (
                    username, str(paper.get("id") or ""),
                    json.dumps(paper, ensure_ascii=False), now,
                    paper.get("publication_date") or None,
                )
                for paper in papers if paper.get("id")
            ],
        )
        # 180일 이상 지난 발견 기록은 정리한다. 북마크·평가 데이터와는 별도다.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        conn.execute(
            "DELETE FROM scholar_digest_cache WHERE username = ? AND discovered_at < ?",
            (username, cutoff),
        )
        conn.commit()


def db_list_scholar_digest_cache(username: str, limit: int = 100) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT paper_json FROM scholar_digest_cache
            WHERE username = ? ORDER BY publication_date DESC, discovered_at DESC LIMIT ?
            """,
            (username, limit),
        ).fetchall()
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["paper_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
    return result


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


# ── 논문 단어장 ──────────────────────────────────────────────────────────────

def db_list_vocabulary_cards(username: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vocabulary_cards WHERE username = ? ORDER BY updated_at DESC, id DESC",
            (username,),
        ).fetchall()
    return [dict(row) for row in rows]


def db_get_vocabulary_card(card_id: int, username: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vocabulary_cards WHERE id = ? AND username = ?",
            (card_id, username),
        ).fetchone()
    return dict(row) if row else None


def db_upsert_vocabulary_card(username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    term = str(payload.get("term") or "").strip()
    normalized = " ".join(term.casefold().split())
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO vocabulary_cards (
                username, doc_id, page_num, term, normalized_term, meaning_ko,
                context_en, context_ko, paper_title, anki_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(username, doc_id, normalized_term) DO UPDATE SET
                page_num = excluded.page_num,
                term = excluded.term,
                meaning_ko = excluded.meaning_ko,
                context_en = excluded.context_en,
                context_ko = excluded.context_ko,
                paper_title = excluded.paper_title,
                anki_status = CASE WHEN vocabulary_cards.anki_note_id IS NULL THEN 'pending' ELSE vocabulary_cards.anki_status END,
                anki_error = NULL,
                updated_at = excluded.updated_at
            """,
            (
                username, payload["doc_id"], payload.get("page_num"), term, normalized,
                str(payload.get("meaning_ko") or "").strip(),
                str(payload.get("context_en") or "").strip(),
                str(payload.get("context_ko") or "").strip(),
                str(payload.get("paper_title") or "").strip(), now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM vocabulary_cards WHERE username = ? AND doc_id = ? AND normalized_term = ?",
            (username, payload["doc_id"], normalized),
        ).fetchone()
        conn.commit()
    return dict(row)


def db_update_vocabulary_sync(card_id: int, username: str, status: str, note_id: Optional[int] = None, error: Optional[str] = None) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE vocabulary_cards
               SET anki_status = ?, anki_note_id = COALESCE(?, anki_note_id),
                   anki_error = ?, updated_at = ?
               WHERE id = ? AND username = ?""",
            (status, note_id, error, datetime.now(timezone.utc).isoformat(), card_id, username),
        )
        conn.commit()


def db_mark_vocabulary_obsidian_synced(card_id: int, username: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE vocabulary_cards SET obsidian_synced = 1 WHERE id = ? AND username = ?",
            (card_id, username),
        )
        conn.commit()


def db_delete_vocabulary_card(card_id: int, username: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM vocabulary_cards WHERE id = ? AND username = ?",
            (card_id, username),
        )
        conn.commit()
        return cursor.rowcount > 0


def db_import_obsidian_vocabulary_cards(username: str, cards: List[Dict[str, Any]]) -> int:
    """외부 Obsidian 덱의 카드를 앱 단어장 목록에도 보이도록 미러링한다."""
    now = datetime.now(timezone.utc).isoformat()
    changed = 0
    with get_db() as conn:
        for card in cards:
            term = str(card.get("term") or "").strip()
            if not term:
                continue
            normalized = " ".join(term.casefold().split())
            cursor = conn.execute(
                """
                INSERT INTO vocabulary_cards (
                    username, doc_id, page_num, term, normalized_term, meaning_ko,
                    context_en, context_ko, paper_title, anki_note_id, anki_status,
                    anki_error, obsidian_synced, created_at, updated_at
                ) VALUES (?, '__obsidian__', NULL, ?, ?, ?, ?, ?, 'Obsidian 단어장', ?, 'synced', NULL, 1, ?, ?)
                ON CONFLICT(username, doc_id, normalized_term) DO UPDATE SET
                    term = excluded.term,
                    meaning_ko = excluded.meaning_ko,
                    context_en = excluded.context_en,
                    context_ko = excluded.context_ko,
                    anki_note_id = COALESCE(excluded.anki_note_id, vocabulary_cards.anki_note_id),
                    anki_status = 'synced',
                    obsidian_synced = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    username, term, normalized,
                    str(card.get("meaning_ko") or "").strip(),
                    str(card.get("context_en") or "").strip(),
                    str(card.get("context_ko") or "").strip(),
                    card.get("anki_note_id"), now, now,
                ),
            )
            changed += 1 if cursor.rowcount else 0
        conn.commit()
    return changed
