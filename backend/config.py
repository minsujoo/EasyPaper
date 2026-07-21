import os
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
TRANS_PROVIDER = os.getenv("TRANS_PROVIDER", "ollama")
TRANS_MODEL = os.getenv("TRANS_MODEL", "gemma4:e4b")
CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "ollama")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemma4:e4b")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "50"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
CACHE_DIR = os.getenv("CACHE_DIR", "./cache")
LIBRARY_DIR = os.getenv("LIBRARY_DIR", "./library")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
PROJECT_ROOT = os.getenv("PROJECT_ROOT", "")
if not PROJECT_ROOT:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_project_root() -> str:
    return PROJECT_ROOT

def get_ollama_host() -> str:
    return OLLAMA_HOST

def is_ollama_host_local() -> bool:
    """설정된 Ollama 호스트가 이 서버 자신(localhost)을 가리키는지 확인합니다.
    원격 호스트를 가리키는 경우 이 서버에 설치 스크립트를 실행하는 것은 의미가 없습니다."""
    from urllib.parse import urlparse
    try:
        hostname = urlparse(OLLAMA_HOST).hostname or ""
    except Exception:
        return False
    return hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def windows_safe_exec_args(cmd):
    """CLI 서브프로세스 실행용 argv를 만듭니다.

    claude/codex/agy는 npm 등을 통해 설치되면 Windows에서 실제 실행 파일이
    아니라 .cmd/.bat 래퍼 스크립트로 배포되는 경우가 많다. asyncio의
    create_subprocess_exec(shell=False)는 Windows의 CreateProcess를 그대로
    쓰는데, 이 API는 .cmd/.bat 파일을 이미지로 인식하지 못해 실제로는 정상
    설치되어 있어도 "[WinError 2] 지정된 파일을 찾을 수 없습니다" 오류로
    실행 자체가 실패한다. Windows에서는 cmd.exe를 경유해 PATHEXT 확장자
    검색·해석을 맡기는 방식으로 이를 우회한다 (macOS/Linux는 기존과 동일).
    """
    import platform
    if platform.system() == "Windows":
        return ["cmd", "/c"] + list(cmd)
    return list(cmd)


def find_ollama_binary():
    # 반환값: 찾은 ollama 실행 파일의 경로(str) 또는 못 찾았을 때 None
    """이 서버에 Ollama CLI가 설치되어 있는지 확인합니다.

    shutil.which("ollama")는 현재 프로세스가 시작될 때 캡처된 PATH만 보므로,
    (특히 Windows에서) 설치 스크립트가 레지스트리의 PATH를 갱신해도 이미 떠있는
    백엔드 프로세스에는 반영되지 않아 방금 설치했거나 이미 정상 동작 중인
    Ollama를 "미설치"로 오판하는 문제가 있었다. PATH 조회에 실패하면 OS별로
    흔히 설치되는 실제 경로들을 직접 확인해 이 문제를 우회한다.
    """
    import os
    import platform
    import shutil

    found = shutil.which("ollama")
    if found:
        return found

    system = platform.system()
    candidates = []
    if system == "Windows":
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            candidates.append(os.path.join(localappdata, "Programs", "Ollama", "ollama.exe"))
    elif system == "Darwin":
        candidates += ["/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"]
    else:
        candidates += ["/usr/local/bin/ollama", "/usr/bin/ollama"]

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None

def get_trans_provider() -> str:
    return TRANS_PROVIDER

def get_trans_model() -> str:
    return TRANS_MODEL

def get_chat_provider() -> str:
    return CHAT_PROVIDER

def get_chat_model() -> str:
    return CHAT_MODEL

def get_openai_api_key() -> str:
    return OPENAI_API_KEY

def get_gemini_api_key() -> str:
    return GEMINI_API_KEY

def get_claude_api_key() -> str:
    return CLAUDE_API_KEY

def update_system_settings(
    ollama_host: str,
    trans_provider: str,
    trans_model: str,
    chat_provider: str,
    chat_model: str,
    openai_api_key: str = "",
    gemini_api_key: str = "",
    claude_api_key: str = ""
):
    global OLLAMA_HOST, TRANS_PROVIDER, TRANS_MODEL, CHAT_PROVIDER, CHAT_MODEL, OPENAI_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY
    
    OLLAMA_HOST = ollama_host
    TRANS_PROVIDER = trans_provider
    TRANS_MODEL = trans_model
    CHAT_PROVIDER = chat_provider
    CHAT_MODEL = chat_model
    OPENAI_API_KEY = openai_api_key
    GEMINI_API_KEY = gemini_api_key
    CLAUDE_API_KEY = claude_api_key
    
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    settings = {
        "OLLAMA_HOST": ollama_host,
        "TRANS_PROVIDER": trans_provider,
        "TRANS_MODEL": trans_model,
        "CHAT_PROVIDER": chat_provider,
        "CHAT_MODEL": chat_model,
        "OPENAI_API_KEY": openai_api_key,
        "GEMINI_API_KEY": gemini_api_key,
        "CLAUDE_API_KEY": claude_api_key
    }

    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in settings.items():
                f.write(f"{k}={v}\n")
        return
        
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    new_lines = []
    found_keys = set()
    
    for line in lines:
        stripped = line.strip()
        updated = False
        for k in settings.keys():
            if stripped.startswith(f"{k}="):
                new_lines.append(f"{k}={settings[k]}\n")
                found_keys.add(k)
                updated = True
                break
        if not updated:
            new_lines.append(line)
            
    for k, v in settings.items():
        if k not in found_keys:
            new_lines.append(f"{k}={v}\n")
            
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# Authentication settings
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
DEFAULT_PASSWORD_HASH = "0102030405060708090a0b0c0d0e0f10:c8c17b1c61732cde577461e36b682deab2dda5cd72797d2517526dfcbc39d6b3"
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", DEFAULT_PASSWORD_HASH)
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "easypaper_secret_key_change_me_in_production_1234567890")

def get_app_username() -> str:
    return APP_USERNAME

def get_app_password_hash() -> str:
    return APP_PASSWORD_HASH

def get_app_password() -> str:
    return APP_PASSWORD

def update_credentials_in_env(new_username: str, new_password_hash: str):
    global APP_USERNAME, APP_PASSWORD_HASH, APP_PASSWORD
    
    APP_USERNAME = new_username
    APP_PASSWORD_HASH = new_password_hash
    APP_PASSWORD = ""  # Clear plaintext password for security
    
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"APP_USERNAME={new_username}\nAPP_PASSWORD_HASH={new_password_hash}\n")
        return
        
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    new_lines = []
    username_found = False
    hash_found = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("APP_USERNAME="):
            new_lines.append(f"APP_USERNAME={new_username}\n")
            username_found = True
        elif stripped.startswith("APP_PASSWORD_HASH="):
            new_lines.append(f"APP_PASSWORD_HASH={new_password_hash}\n")
            hash_found = True
        elif stripped.startswith("APP_PASSWORD="):
            # Deactivate plaintext password by commenting it out
            new_lines.append(f"# APP_PASSWORD=\n")
        else:
            new_lines.append(line)
            
    if not username_found:
        new_lines.append(f"APP_USERNAME={new_username}\n")
    if not hash_found:
        new_lines.append(f"APP_PASSWORD_HASH={new_password_hash}\n")
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

# 디렉토리 생성
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(LIBRARY_DIR, exist_ok=True)

# App host & port configuration
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

# Antigravity CLI path
AGY_PATH = os.getenv("AGY_PATH", "/home/ubuntu/.local/bin/agy")
CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "/home/ubuntu/.local/bin/claude")
CODEX_PATH = os.getenv("CODEX_PATH", "/home/ubuntu/.local/bin/codex")

def get_app_host() -> str:
    return APP_HOST

def get_app_port() -> int:
    return APP_PORT

def _resolve_cli_path(configured_path: str, bare_command: str) -> str:
    """설정된 CLI 경로가 실제로 존재하면 그대로 쓰고, 없으면 PATH에서 명령
    이름으로 직접 찾는다.

    AGY_PATH/CLAUDE_CODE_PATH/CODEX_PATH의 기본값은 이 프로젝트를 개발한
    서버 환경 기준의 고정 경로(/home/ubuntu/.local/bin/...)라, 다른 사용자
    계정이나 macOS/Windows 등 다른 환경에서는 애초에 존재하지 않는 경로다.
    이 경우 자동 감지가 항상 실패해 실제로는 정상 설치되어 있는 CLI도
    "미설치"로 오판하게 된다. 설정된 경로가 없으면 PATH 검색으로 대체하고,
    거기서도 못 찾으면 bare 명령 이름을 그대로 반환해 최소한 셸(PATH) 기반
    실행이라도 시도해볼 수 있게 한다.
    """
    import os
    import shutil
    if configured_path and os.path.exists(configured_path):
        return configured_path
    found = shutil.which(bare_command)
    if found:
        return found
    return bare_command

def get_agy_path() -> str:
    return _resolve_cli_path(AGY_PATH, "agy")

def get_claude_code_path() -> str:
    return _resolve_cli_path(CLAUDE_CODE_PATH, "claude")

def get_codex_path() -> str:
    return _resolve_cli_path(CODEX_PATH, "codex")

def get_agy_env() -> dict:
    env = os.environ.copy()
    if "HOME" not in env or not env["HOME"]:
        env["HOME"] = "/home/ubuntu"
    if "USER" not in env or not env["USER"]:
        env["USER"] = "ubuntu"
    return env

# ── Dynamic Translation Prompt Template ─────────────────
PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation_prompt.txt")

DEFAULT_PROMPT_TEMPLATE = """당신은 학술 논문 번역 전문가입니다. {{LANG_INSTRUCTION}}

번역 스타일:
{{STYLE_INSTRUCTION}}

번역 규칙:
{{RULES_TEXT}}
- 번역 텍스트를 중간에 절대 끊지 마세요. 반드시 주어진 원문 전체를 빠짐없이 완전하게 번역하여 출력하세요.
- 번역 시작 전 서론(예: '번역 결과:', '다음은 번역입니다:')을 절대 추가하지 마세요. 번역된 내용만 즉시 출력하세요.
- 제공된 [참고 문맥 정보](논문 제목 및 이전 번역)를 참고하여, 전문 용어 번역이나 문장 어조가 일관되게 이어지도록 하세요. 단, 이전 번역 내용을 답변에 다시 포함하여 출력해서는 안 되며, 오직 아래의 '원문'만 새로 번역해야 합니다.
- [여기에 본인만의 추가 번역 규칙이나 어투 지시사항(예: "반드시 경어체 '~합니다' 체만 사용하여 자연스럽게 번역해 주세요")을 자유롭게 입력하여 수정할 수 있습니다]

{{CONTEXT_PART}}

원문:
{{TEXT}}

{{TARGET_LANG}} 번역:"""

def get_translation_prompt_template() -> str:
    if not os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PROMPT_TEMPLATE.strip())
        return DEFAULT_PROMPT_TEMPLATE.strip()
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

def update_translation_prompt_template(new_template: str):
    if not new_template or not new_template.strip():
        # Fall back to default if empty
        template_to_save = DEFAULT_PROMPT_TEMPLATE.strip()
    else:
        template_to_save = new_template.strip()
    with open(PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(template_to_save)

