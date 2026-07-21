from fastapi import APIRouter, Response, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
import json
import httpx
from pydantic import BaseModel
from services.auth import verify_password, hash_password, create_session_token, get_current_user
from config import (
    get_app_username,
    get_app_password_hash,
    update_credentials_in_env,
    get_ollama_host,
    is_ollama_host_local,
    update_system_settings,
    get_trans_provider,
    get_trans_model,
    get_chat_provider,
    get_chat_model,
    get_openai_api_key,
    get_gemini_api_key,
    get_claude_api_key,
    get_translation_prompt_template,
    update_translation_prompt_template,
    get_agy_path,
    get_claude_code_path,
    get_codex_path,
    find_ollama_binary,
)
from services.llm_client import check_ollama_health


async def _stream_subprocess_lines(proc):
    """서브프로세스의 stdout을 한 줄씩 비동기로 yield합니다 (설치 스크립트 진행 로그 스트리밍용).

    클라이언트가 SSE 연결을 중간에 끊으면(설치 진행 중 브라우저 탭을 닫는 등)
    FastAPI/Starlette가 이 async generator를 GeneratorExit로 강제 종료하는데,
    그 시점에 설치 서브프로세스가 아직 살아있으면 아무도 기다려주지 않는
    좀비/유령 프로세스로 남는다. finally에서 아직 살아있으면 항상 죽이고
    회수해, 취소된 설치가 반복될 때마다 프로세스가 계속 쌓이지 않게 한다.
    """
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                yield text
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

router = APIRouter()

class LoginRequest(BaseModel):
    username: str
    password: str

class ChangeCredentialsRequest(BaseModel):
    current_password: str
    new_username: str
    new_password: str

@router.post("/auth/login")
async def login(response: Response, data: LoginRequest):
    from services.db import get_user
    user = get_user(data.username)
    
    if not user or not verify_password(user["password_hash"], data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    
    token = create_session_token(data.username)
    
    # 보안 강화를 위해 HttpOnly, SameSite=Lax 적용 쿠키로 토큰 주입
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        max_age=7 * 24 * 3600,  # 7일
        expires=7 * 24 * 3600,
        samesite="lax",
        secure=False,  # 로컬 개발 환경 및 내부망 접속 대응용 (HTTPS 운영 시 True 변경 권장)
        path="/"
    )
    return {"message": "로그인 성공", "username": data.username}

@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(
        key="session_token",
        path="/"
    )
    return {"message": "로그아웃 성공"}

@router.get("/auth/check")
async def check_auth(username: str = Depends(get_current_user)):
    return {"status": "authenticated", "username": username}

@router.post("/auth/change-credentials")
async def change_credentials(
    response: Response, 
    data: ChangeCredentialsRequest, 
    current_user: str = Depends(get_current_user)
):
    from services.db import get_user, update_user_credentials
    user = get_user(current_user)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="사용자를 찾을 수 없습니다."
        )
        
    current_password_hash = user["password_hash"]
    
    # 현재 비밀번호 검증
    if not verify_password(current_password_hash, data.current_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="현재 비밀번호가 일치하지 않습니다."
        )
        
    new_username = data.new_username.strip()
    new_password = data.new_password.strip()
    
    if not new_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 아이디를 입력해주세요."
        )
        
    if not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호를 입력해주세요."
        )

    # 새 비밀번호가 현재 비밀번호와 동일한지 확인
    if verify_password(current_password_hash, new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="새 비밀번호는 현재 비밀번호와 다르게 설정해야 합니다."
        )

        
    # 새로운 해시 생성 및 DB + .env 업데이트
    new_hash = hash_password(new_password)
    if not update_user_credentials(current_user, new_username, new_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이미 존재하는 아이디입니다."
        )
    update_credentials_in_env(new_username, new_hash)
    
    # 세션 갱신
    new_token = create_session_token(new_username)
    response.set_cookie(
        key="session_token",
        value=new_token,
        httponly=True,
        max_age=7 * 24 * 3600,
        expires=7 * 24 * 3600,
        samesite="lax",
        secure=False,
        path="/"
    )
    
    return {"message": "아이디 및 비밀번호가 성공적으로 변경되었습니다.", "username": new_username}

class SystemSettingsRequest(BaseModel):
    ollama_host: str
    trans_provider: str
    trans_model: str
    chat_provider: str
    chat_model: str
    openai_api_key: str = ""
    gemini_api_key: str = ""
    claude_api_key: str = ""
    translation_prompt_template: str = ""

@router.get("/settings/system")
async def get_system_settings(current_user: str = Depends(get_current_user)):
    health = await check_ollama_health()
    available_models = health.get("available_models", [])
    
    return {
        "ollama_host": get_ollama_host(),
        "available_models": available_models,
        "trans_provider": get_trans_provider(),
        "trans_model": get_trans_model(),
        "chat_provider": get_chat_provider(),
        "chat_model": get_chat_model(),
        "openai_api_key": get_openai_api_key(),
        "gemini_api_key": get_gemini_api_key(),
        "claude_api_key": get_claude_api_key(),
        "translation_prompt_template": get_translation_prompt_template()
    }

@router.post("/settings/system")
async def save_system_settings(data: SystemSettingsRequest, current_user: str = Depends(get_current_user)):
    trans_provider = data.trans_provider.strip().lower()
    chat_provider = data.chat_provider.strip().lower()
    
    valid_providers = ["ollama", "openai", "gemini", "claude", "antigravity", "claude_code", "codex"]
    if trans_provider not in valid_providers or chat_provider not in valid_providers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="올바르지 않은 AI 제공업체입니다."
        )
        
    update_system_settings(
        ollama_host=data.ollama_host.strip(),
        trans_provider=trans_provider,
        trans_model=data.trans_model.strip(),
        chat_provider=chat_provider,
        chat_model=data.chat_model.strip(),
        openai_api_key=data.openai_api_key.strip(),
        gemini_api_key=data.gemini_api_key.strip(),
        claude_api_key=data.claude_api_key.strip()
    )
    
    # 고급 설정: 번역 프롬프트 템플릿 저장
    update_translation_prompt_template(data.translation_prompt_template)
    
    return {"message": "시스템 설정이 성공적으로 변경되었습니다."}

@router.get("/settings/ollama-status")
async def ollama_status(current_user: str = Depends(get_current_user)):
    """Ollama CLI가 이 서버에 설치되어 있는지, 설정된 호스트가 로컬인지 확인합니다."""
    installed = bool(find_ollama_binary())
    return {
        "installed": installed,
        "is_local": is_ollama_host_local(),
    }


@router.get("/settings/install-ollama")
async def install_ollama_stream(current_user: str = Depends(get_current_user)):
    """이 서버의 운영체제에 맞는 방법으로 Ollama를 설치하고 진행 상황을 스트리밍합니다.
    Linux는 공식 설치 스크립트, macOS는 Homebrew(있는 경우), Windows는 공식 설치
    프로그램을 내려받아 무인 설치합니다. 원격 호스트를 가리키고 있거나 이미 설치된
    경우에는 실행하지 않습니다."""
    import asyncio
    import os
    import platform
    import shutil
    import tempfile

    system = platform.system()  # 'Linux' | 'Darwin' | 'Windows'

    async def event_stream():
        if not is_ollama_host_local():
            yield f"data: {json.dumps({'status': 'error', 'message': 'Ollama 호스트가 이 서버(localhost)가 아니어서 여기서 설치할 수 없습니다.'})}\n\n"
            return
        if find_ollama_binary():
            yield f"data: {json.dumps({'status': 'error', 'message': 'Ollama가 이미 설치되어 있습니다.'})}\n\n"
            return

        try:
            if system == "Linux":
                proc = await asyncio.create_subprocess_shell(
                    "curl -fsSL https://ollama.com/install.sh | sh",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    # sudo가 비밀번호를 요구하면 무한 대기하지 않고 즉시 실패하도록 stdin을 막는다
                    stdin=asyncio.subprocess.DEVNULL,
                )
                async for text in _stream_subprocess_lines(proc):
                    yield f"data: {json.dumps({'status': 'progress', 'line': text})}\n\n"
                await proc.wait()
                if proc.returncode == 0 and find_ollama_binary():
                    yield f"data: {json.dumps({'status': 'success'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'설치 스크립트가 오류 코드 {proc.returncode}로 종료되었습니다. sudo 권한이 필요할 수 있습니다.'})}\n\n"

            elif system == "Darwin":
                if not shutil.which("brew"):
                    yield f"data: {json.dumps({'status': 'error', 'message': 'Homebrew가 설치되어 있지 않아 이 서버에서 자동 설치할 수 없습니다. https://ollama.com/download/mac 에서 직접 다운로드해 설치해주세요.'})}\n\n"
                    return
                yield f"data: {json.dumps({'status': 'progress', 'line': 'Homebrew로 Ollama를 설치합니다 (brew install ollama)...'})}\n\n"
                proc = await asyncio.create_subprocess_shell(
                    "brew install ollama",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                async for text in _stream_subprocess_lines(proc):
                    yield f"data: {json.dumps({'status': 'progress', 'line': text})}\n\n"
                await proc.wait()
                if proc.returncode == 0 and find_ollama_binary():
                    yield f"data: {json.dumps({'status': 'success'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'brew install이 오류 코드 {proc.returncode}로 종료되었습니다.'})}\n\n"

            elif system == "Windows":
                yield f"data: {json.dumps({'status': 'progress', 'line': 'Ollama 설치 프로그램을 다운로드합니다...'})}\n\n"
                installer_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
                try:
                    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                        async with client.stream("GET", "https://ollama.com/download/OllamaSetup.exe") as resp:
                            resp.raise_for_status()
                            with open(installer_path, "wb") as f:
                                async for chunk in resp.aiter_bytes():
                                    f.write(chunk)
                except Exception as e:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'설치 프로그램 다운로드 실패: {e}. https://ollama.com/download/windows 에서 직접 다운로드해주세요.'})}\n\n"
                    return

                yield f"data: {json.dumps({'status': 'progress', 'line': '다운로드 완료. 자동으로 설치를 진행합니다...'})}\n\n"
                proc = await asyncio.create_subprocess_exec(
                    installer_path, "/VERYSILENT", "/NORESTART",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                async for text in _stream_subprocess_lines(proc):
                    yield f"data: {json.dumps({'status': 'progress', 'line': text})}\n\n"
                await proc.wait()
                if proc.returncode == 0 and find_ollama_binary():
                    yield f"data: {json.dumps({'status': 'success'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'설치 프로그램이 종료 코드 {proc.returncode}로 끝났습니다. https://ollama.com/download/windows 에서 직접 설치해주세요.'})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'error', 'message': f'지원하지 않는 운영체제({system})입니다. https://ollama.com/download 에서 직접 설치해주세요.'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


def _make_npm_cli_install_endpoint(package_name: str, path_getter, already_installed_message: str):
    """claude_code/codex처럼 npm 전역 패키지로 배포되는 CLI를 설치하는 SSE 스트림을 만듭니다.
    Node.js/npm은 세 OS 모두 이미 EasyPaper의 필수 요구사항이라, 셸을 통한
    `npm install -g <package>` 한 줄로 플랫폼 분기 없이 동일하게 동작합니다."""
    async def stream(current_user: str = Depends(get_current_user)):
        import asyncio
        import os
        import shutil

        async def event_stream():
            cli_path = path_getter()
            if os.path.exists(cli_path) or shutil.which(cli_path):
                yield f"data: {json.dumps({'status': 'error', 'message': already_installed_message})}\n\n"
                return
            if not shutil.which("npm"):
                npm_missing_message = "'npm' 명령을 찾을 수 없습니다. Node.js가 설치되어 있는지 확인해주세요."
                yield f"data: {json.dumps({'status': 'error', 'message': npm_missing_message})}\n\n"
                return

            try:
                proc = await asyncio.create_subprocess_shell(
                    f"npm install -g {package_name}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                async for text in _stream_subprocess_lines(proc):
                    yield f"data: {json.dumps({'status': 'progress', 'line': text})}\n\n"
                await proc.wait()
                if proc.returncode == 0:
                    yield f"data: {json.dumps({'status': 'success'})}\n\n"
                else:
                    yield f"data: {json.dumps({'status': 'error', 'message': f'설치가 오류 코드 {proc.returncode}로 종료되었습니다.'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    return stream


router.add_api_route(
    "/settings/install-claude-code",
    _make_npm_cli_install_endpoint("@anthropic-ai/claude-code", get_claude_code_path, "Claude Code CLI가 이미 설치되어 있습니다."),
    methods=["GET"],
)

router.add_api_route(
    "/settings/install-codex",
    _make_npm_cli_install_endpoint("@openai/codex", get_codex_path, "Codex CLI가 이미 설치되어 있습니다."),
    methods=["GET"],
)


@router.get("/settings/install-antigravity")
async def install_antigravity_stream(current_user: str = Depends(get_current_user)):
    """공식 설치 스크립트(https://antigravity.google/cli)로 이 서버에 Antigravity CLI(agy)를
    설치하고 진행 상황을 스트리밍합니다."""
    import asyncio
    import os
    import platform
    import shutil

    system = platform.system()  # 'Linux' | 'Darwin' | 'Windows'

    async def event_stream():
        agy_path = get_agy_path()
        if os.path.exists(agy_path) or shutil.which(agy_path) or shutil.which("agy"):
            yield f"data: {json.dumps({'status': 'error', 'message': 'Antigravity CLI가 이미 설치되어 있습니다.'})}\n\n"
            return

        if system == "Windows":
            # cmd.exe용 공식 설치 스크립트를 %TEMP%에 받아 실행 후 정리한다.
            command = (
                'curl -fsSL https://antigravity.google/cli/install.cmd -o "%TEMP%\\antigravity_install.cmd" '
                '&& call "%TEMP%\\antigravity_install.cmd" '
                '&& del "%TEMP%\\antigravity_install.cmd"'
            )
        else:
            # macOS와 Linux는 동일한 공식 셸 스크립트를 사용한다.
            command = "curl -fsSL https://antigravity.google/cli/install.sh | bash"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
            async for text in _stream_subprocess_lines(proc):
                yield f"data: {json.dumps({'status': 'progress', 'line': text})}\n\n"
            await proc.wait()

            agy_path_after = get_agy_path()
            installed = os.path.exists(agy_path_after) or shutil.which(agy_path_after) or shutil.which("agy")
            if proc.returncode == 0 and installed:
                yield f"data: {json.dumps({'status': 'success'})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'error', 'message': f'설치 스크립트가 오류 코드 {proc.returncode}로 종료되었습니다.'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.get("/settings/pull-model")
async def pull_model_stream(model_name: str, current_user: str = Depends(get_current_user)):
    """Ollama 서버에 새로운 모델 다운로드를 요청하고 진행 상황을 스트리밍합니다."""
    model_name = model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="모델명을 입력해주세요.")
        
    async def event_stream():
        payload = {"name": model_name, "stream": True}
        try:
            # Ollama API에 스트리밍 요청 발송
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                async with client.stream(
                    "POST",
                    f"{get_ollama_host()}/api/pull",
                    json=payload
                ) as response:
                    if response.status_code != 200:
                        yield f"data: {json.dumps({'status': 'error', 'message': 'Ollama 서버 응답 에러'})}\n\n"
                        return
                        
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        yield f"data: {line.strip()}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"
            
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


def _is_running_under_systemd() -> bool:
    """이 프로세스가 systemd 유닛으로 실행 중인지 확인합니다.

    systemd는 v232부터 자신이 띄운 모든 프로세스에 유닛 실행마다 고유한
    INVOCATION_ID 환경변수를 심어준다 - 이 프로젝트의 easypaper.service도
    ExecStart로 직접 python main.py를 실행하므로 이 값이 신뢰할 수 있는
    판별 근거가 된다.
    """
    import os
    return bool(os.environ.get("INVOCATION_ID"))


async def _restart_server_process(project_dir: str):
    """서버를 재시작합니다. Linux에서 systemd로 실행 중이면 systemctl로, 그 외의
    모든 경우(Windows/macOS, 또는 scripts/sh/start.sh·scripts\\bat\\start.bat로
    터미널에서 직접 실행한 Linux)에는 스스로 새 프로세스를 미리 띄워두고
    자신은 종료하는 방식으로 재시작합니다.

    예전에는 `sudo systemctl restart easypaper`만 무조건 실행했는데, 이는
    Linux + systemd 조합에서만 동작하는 명령이다. Windows에는 systemctl/sudo
    자체가 없고, macOS는 systemd가 없어 커맨드 자체가 즉시 실패한다. 게다가
    이 호출은 "쏘고 잊는"(fire-and-forget) 백그라운드 태스크라 실패해도
    사용자에게는 "업데이트 성공"이라고 응답이 나간 뒤라 아무도 알아채지
    못하고, 실제로는 서버가 예전 코드로 계속 돌아가는 상태로 남았다.
    """
    import asyncio
    import os
    import platform
    import shutil
    import subprocess
    import sys

    await asyncio.sleep(1.0)

    if platform.system() == "Linux" and _is_running_under_systemd() and shutil.which("systemctl"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "easypaper",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            await proc.communicate()
            return
        except Exception as e:
            print(f"[system_update] systemctl restart 실패, 자체 재시작으로 대체: {e}", flush=True)

    # systemd가 아닌 환경 - 같은 명령으로 새 프로세스를 미리(포트가 풀릴 시간을
    # 두고) 띄워둔 뒤 현재 프로세스를 종료해 사실상 스스로를 재시작한다.
    backend_dir = os.path.join(project_dir, "backend")
    python_exe = sys.executable
    delay_launch_script = (
        "import subprocess, sys, time\n"
        "time.sleep(3)\n"
        f"subprocess.Popen([{python_exe!r}, 'main.py'], cwd={backend_dir!r})\n"
    )

    popen_kwargs = {"cwd": backend_dir}
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen([python_exe, "-c", delay_launch_script], **popen_kwargs)
    except Exception as e:
        print(f"[system_update] 자체 재시작 프로세스 실행 실패: {e}", flush=True)
        return

    await asyncio.sleep(0.3)
    # 새 프로세스가 이미 대기 중이므로, 현재 프로세스는 정리 절차 없이 즉시
    # 종료해 리스닝 중인 포트를 최대한 빨리 비워준다.
    os._exit(0)


@router.post("/settings/update")
async def system_update(current_user: str = Depends(get_current_user)):
    """깃허브 최신 커밋을 풀(pull) 받고, 프론트엔드를 빌드한 뒤 서버를 재기동합니다."""
    import subprocess
    import asyncio
    import os

    try:
        # 프로젝트 루트 디렉토리 찾기
        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        
        # 1. git pull origin main
        pull_proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "main",
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await pull_proc.communicate()
        if pull_proc.returncode != 0:
            return {
                "ok": False,
                "message": f"Git pull 실패: {stderr.decode('utf-8', errors='replace')}"
            }
        
        pull_output = stdout.decode('utf-8', errors='replace')
        
        # 2. 만약 pull 된 내용이 있으면 (또는 무조건 안전하게) 프론트엔드를 빌드합니다.
        if "Already up-to-date" not in pull_output and "Already up to date" not in pull_output:
            frontend_dir = os.path.join(project_dir, "frontend")
            if os.path.exists(frontend_dir):
                # npm install && npm run build
                from config import windows_safe_exec_args
                build_proc = await asyncio.create_subprocess_exec(
                    *windows_safe_exec_args(["npm", "run", "build"]),
                    cwd=frontend_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                build_stdout, build_stderr = await build_proc.communicate()
                if build_proc.returncode != 0:
                    return {
                        "ok": False,
                        "message": f"프론트엔드 빌드 실패: {build_stderr.decode('utf-8', errors='replace')}"
                    }
        
        # 3. 비동기로 1초 후에 서버 재시작 (OS/실행 방식에 맞춰 자동 판단)
        asyncio.create_task(_restart_server_process(project_dir))
        
        return {
            "ok": True,
            "message": "업데이트가 성공적으로 적용되었습니다. 서버가 1초 후에 재시작됩니다.",
            "output": pull_output
        }
        
    except Exception as e:
        return {
            "ok": False,
            "message": f"업데이트 중 알 수 없는 오류 발생: {str(e)}"
        }



