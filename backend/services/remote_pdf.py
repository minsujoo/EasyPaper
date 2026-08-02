"""공개 HTTPS PDF를 로컬 임시 파일로 안전하게 내려받는 공용 도우미."""

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

import aiofiles
import httpx


class RemotePdfError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


async def _ensure_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RemotePdfError("안전하지 않은 PDF 주소입니다.", 400)

    def resolve_addresses():
        return socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)

    try:
        infos = await asyncio.to_thread(resolve_addresses)
    except OSError as exc:
        raise RemotePdfError("PDF 서버 주소를 확인할 수 없습니다.") from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise RemotePdfError("안전하지 않은 PDF 주소입니다.", 400)


async def download_public_pdf(url: str, destination: str, max_bytes: int) -> int:
    """리다이렉트마다 주소를 검증하고, 파일을 메모리에 올리지 않고 스트리밍한다."""
    current_url = (url or "").strip()
    try:
        async with httpx.AsyncClient(timeout=40.0, follow_redirects=False) as client:
            for _ in range(6):
                await _ensure_public_https(current_url)
                async with client.stream(
                    "GET", current_url,
                    headers={"User-Agent": "Scholar desktop research paper downloader/0.1"},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise RemotePdfError("PDF 다운로드 주소가 올바르지 않습니다.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code != 200:
                        raise RemotePdfError(f"PDF를 내려받지 못했습니다. (HTTP {response.status_code})")
                    total = 0
                    first = b""
                    async with aiofiles.open(destination, "wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            if not chunk:
                                continue
                            if not first:
                                first = chunk[:8]
                            total += len(chunk)
                            if total > max_bytes:
                                raise RemotePdfError(
                                    f"PDF 파일이 {max_bytes // (1024 * 1024)}MB를 초과합니다.", 413,
                                )
                            await output.write(chunk)
                    if not first.startswith(b"%PDF"):
                        try:
                            os.remove(destination)
                        except FileNotFoundError:
                            pass
                        raise RemotePdfError("다운로드 결과가 PDF 파일이 아닙니다.")
                    return total
            raise RemotePdfError("PDF 다운로드 리다이렉트가 너무 많습니다.")
    except RemotePdfError:
        raise
    except httpx.HTTPError as exc:
        raise RemotePdfError("PDF 서버에 연결하지 못했습니다.") from exc
