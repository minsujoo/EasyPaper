from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import contextlib
import os
import logging

from logging_config import setup_logging
setup_logging()
logger = logging.getLogger(__name__)

from config import CORS_ORIGINS, UPLOAD_DIR, APP_HOST, APP_PORT, get_anki_auto_launch
from routers import upload, translate, chat
from routers import library as library_router
from routers import jobs as jobs_router
from routers import auth as auth_router
from routers import agy as agy_router
from routers import insight as insight_router
from routers import primer as primer_router
from routers import notes as notes_router
from routers import paper_search as paper_search_router
from routers import vocabulary as vocabulary_router
from routers import sync_control as sync_control_router
from services.auth import get_current_user

app = FastAPI(
    title="EasyPaper API",
    description="PDF 논문 번역 서비스 (Gemma 4 E4B + Ollama)",
    version="1.0.0",
)

# CORS 설정 (모든 오리진 허용 — NPM/리버스 프록시 환경)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(auth_router.router, prefix="/api", tags=["Auth"])
app.include_router(upload.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Upload"])
app.include_router(translate.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Translate"])
app.include_router(chat.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Chat"])
app.include_router(library_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Library"])
app.include_router(jobs_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Jobs"])
app.include_router(agy_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["AGY"])
app.include_router(insight_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Insight"])
app.include_router(primer_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Primer"])
app.include_router(notes_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Notes"])
app.include_router(paper_search_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Paper Search"])
app.include_router(vocabulary_router.router, prefix="/api", dependencies=[Depends(get_current_user)], tags=["Vocabulary"])
app.include_router(sync_control_router.router, prefix="/api", tags=["Sync"])


@app.on_event("startup")
async def startup_event():
    """서버 시작 시 데이터베이스 초기화 및 라이브러리의 문서들을 세션으로 복원합니다."""
    from services.db import init_db
    from services.usage_tracker import init_usage_table
    init_db()
    init_usage_table()
    upload.restore_sessions_from_library()
    # 내장 단어장/복습은 Anki 없이도 작동한다. AnkiConnect로 외부 덱까지
    # 동기화하려는 사용자가 명시적으로 자동 실행을 켠 경우에만 시작한다.
    if get_anki_auto_launch():
        from services.anki import launch_anki
        launch_anki()
    # 24시간 간격으로 새 학술 레코드를 로컬 캐시에 모은다. 앱이 꺼져 있던
    # 기간은 다음 실행 직후 한 번 수집하여 놓친 논문 피드에 합친다.
    from services.scholar_crawler import scholar_crawl_loop
    app.state.scholar_crawl_task = asyncio.create_task(scholar_crawl_loop())
    from services.conference_official import conference_refresh_loop
    app.state.conference_refresh_task = asyncio.create_task(conference_refresh_loop())
    from services.scholar_tools import install_scholar_user_timer
    app.state.scholar_timer_status = await asyncio.to_thread(install_scholar_user_timer)
    # 중앙 서버가 아직 설정되지 않았을 때는 잠자기만 하며, 설정 화면에서 URL과
    # 토큰을 저장하면 앱 재시작 없이 다음 주기부터 활성화된다.
    from services.sync_client import init_local_sync_schema, sync_loop
    init_local_sync_schema()
    app.state.sync_task = asyncio.create_task(sync_loop())


@app.on_event("shutdown")
async def shutdown_event():
    for name in ("scholar_crawl_task", "conference_refresh_task", "sync_task"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@app.get("/api/pdf-file/{session_id}")
async def serve_pdf(session_id: str, username: str = Depends(get_current_user)):
    """PDF 파일을 직접 서빙합니다."""
    session = upload.require_session_owner(session_id, username)
    return FileResponse(session["pdf_path"], media_type="application/pdf")


# 프론트엔드 정적 파일 서빙 (빌드된 dist 폴더)
#
# EASYPAPER_FRONTEND_DIST가 있으면 그 경로를 그대로 쓴다. PyInstaller onedir로
# 패키징된 sidecar에서는 main.py의 __file__이 <onedir>/_internal/main.py를
# 가리키게 되는데, PyInstaller가 datas 목적지로 "<onedir> 최상위 밖"(예:
# "../frontend/dist")을 허용하지 않아 dist를 onedir 최상위(main.py가 기존
# 상대경로로 찾는 위치)에 둘 수 없다. 대신 dist를 _internal/frontend/dist에
# 두고 이 env var로 실제 위치를 알려준다. 미설정 시(서버/Docker 배포)에는
# 기존과 동일하게 상대경로로 계산한다.
FRONTEND_DIST = os.getenv("EASYPAPER_FRONTEND_DIST") or os.path.join(os.path.dirname(__file__), "../frontend/dist")
if os.path.exists(FRONTEND_DIST):
    # /assets 등 정적 자산
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str = ""):
        """SPA 라우팅 — 모든 경로를 index.html로 폴백 (API 경로 제외, 루트 정적 파일은 직접 서빙)"""
        if full_path.startswith("api"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404)

        if full_path:
            # os.path.join + isfile만으로는 "../"가 섞인 경로를 걸러내지 못해
            # dist 밖의 임의 파일을 읽어올 수 있다(Starlette가 대부분의 "../"
            # 케이스를 라우팅 단계에서 이미 정규화해주긴 하지만, 그 동작에만
            # 의존하지 않고 실제 해석된 절대경로가 dist 안에 있는지 명시적으로
            # 검증한다).
            dist_root = os.path.realpath(FRONTEND_DIST)
            file_path = os.path.realpath(os.path.join(FRONTEND_DIST, full_path))
            if file_path.startswith(dist_root + os.sep) and os.path.isfile(file_path):
                return FileResponse(file_path)

        index = os.path.join(FRONTEND_DIST, "index.html")
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
else:
    @app.get("/")
    async def root():
        return {"message": "EasyPaper API is running — 프론트엔드 빌드 필요 (npm run build)", "docs": "/docs"}


if __name__ == "__main__":
    import sys
    if "--scholar-crawl-once" in sys.argv:
        from services.db import init_db
        from services.scholar_crawler import refresh_scholar_cache
        from services.conference_official import refresh_official_conferences
        from config import get_app_username
        init_db()
        async def scheduled_refresh():
            scholar, conferences = await asyncio.gather(
                refresh_scholar_cache(get_app_username(), force=True),
                refresh_official_conferences(force=True),
                return_exceptions=True,
            )
            return {"scholar": scholar, "conferences": conferences}
        result = asyncio.run(scheduled_refresh())
        logger.info("scheduled research refresh complete: %s", result)
    else:
        import uvicorn
        uvicorn.run(app, host=APP_HOST, port=APP_PORT, reload=False)
