from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config import get_sync_settings, update_sync_settings
from services.auth import get_current_user
from services.sync_client import get_sync_status, sync_once_async
from services.vault_sync import get_vault_sync_status, run_vault_sync


router = APIRouter()


class SyncSettingsRequest(BaseModel):
    server_url: str = ""
    token: str | None = None
    interval_seconds: int = Field(default=300, ge=30, le=86400)


class VaultSyncRequest(BaseModel):
    vault_root: str = Field(min_length=1, max_length=4096)
    scope: str = Field(default="primary", min_length=1, max_length=100)


@router.get("/settings/sync")
async def read_sync_settings(current_user: str = Depends(get_current_user)):
    return {**get_sync_settings(), "runtime": get_sync_status()}


@router.post("/settings/sync")
async def save_sync_settings(data: SyncSettingsRequest, current_user: str = Depends(get_current_user)):
    url = data.server_url.strip().rstrip("/")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=400, detail="동기화 서버 URL은 http:// 또는 https:// 주소여야 합니다.")
    settings = update_sync_settings(url, data.token, data.interval_seconds)
    return {**settings, "runtime": get_sync_status()}


@router.post("/sync/run")
async def run_sync_now(current_user: str = Depends(get_current_user)):
    try:
        return await sync_once_async(username=current_user)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"중앙 동기화에 실패했습니다: {exc}") from exc


@router.get("/sync/status")
async def read_sync_status(current_user: str = Depends(get_current_user)):
    return get_sync_status()


@router.post("/sync/vault/run")
async def run_vault_sync_now(data: VaultSyncRequest, current_user: str = Depends(get_current_user)):
    import asyncio

    try:
        return await asyncio.to_thread(
            run_vault_sync,
            data.vault_root,
            scope=data.scope,
            username=current_user,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vault 동기화에 실패했습니다: {exc}") from exc


@router.get("/sync/vault/status")
async def read_vault_sync_status(current_user: str = Depends(get_current_user)):
    return get_vault_sync_status()
