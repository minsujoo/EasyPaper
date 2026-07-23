use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use tauri::Manager;

/// CreateProcess에 이 플래그를 주지 않으면, PyInstaller onedir sidecar는
/// 콘솔 서브시스템 실행파일이라 Windows가 새 콘솔 창을 띄워준다. Tauri
/// 앱(윈도우 서브시스템, 콘솔 없음)이 부모라 자기 콘솔을 물려줄 수 없기
/// 때문이다. 그 창은 sidecar 프로세스 자신의 콘솔이라, 사용자가 그 창을
/// 닫으면(X 버튼) 콘솔 종료 이벤트가 발생해 sidecar 프로세스 자체가 함께
/// 죽어버린다. stdout/stderr는 Stdio::piped()로 파이프에 리다이렉트되므로
/// 콘솔 창 자체를 아예 안 만들어도(CREATE_NO_WINDOW) 로그 캡처에는 영향이
/// 없다. PyInstaller를 windowed(--noconsole)로 다시 빌드하는 대신 이 방식을
/// 쓰는 이유: PyInstaller의 windowed 모드는 버전에 따라 sys.stdout/stderr가
/// None이 되어 print()가 있는 코드가 죽는 경우가 있어(콘솔이 아예 없다고
/// 가정), 콘솔 서브시스템은 그대로 두고 창만 숨기는 편이 더 안전하다.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// 스폰된 백엔드 sidecar의 자식 프로세스 핸들. 창 종료/앱 종료 시 확실히
/// kill하기 위해 앱 상태로 보관한다(고아 프로세스 방지, 계획 Phase 3).
struct SidecarState(Mutex<Option<Child>>);

/// 선호 포트(8000)를 우선 시도하고, 이미 사용 중이면(기존 웹 배포판이 로컬에
/// 동시 실행 중인 경우 등) OS가 골라주는 임시 포트로 폴백한다(계획 Phase 2).
fn find_available_port(preferred: u16) -> u16 {
    if TcpListener::bind(("127.0.0.1", preferred)).is_ok() {
        return preferred;
    }
    let listener = TcpListener::bind(("127.0.0.1", 0)).expect("failed to bind ephemeral port");
    listener.local_addr().unwrap().port()
}

/// 백엔드의 루트(`/`)에 재시도 GET을 보내 준비 상태를 확인한다. `/api/*`는
/// 전역 인증이 걸려 있어 로그인 전 판별에 쓸 수 없지만, 루트는 인증 없이
/// 200을 반환하며 기존 Dockerfile의 HEALTHCHECK도 동일하게 `/`를 쓴다.
fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            let request =
                format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
            if stream.write_all(request.as_bytes()).is_ok() {
                let mut buf = [0u8; 32];
                if let Ok(n) = stream.read(&mut buf) {
                    if n > 0 {
                        let text = String::from_utf8_lossy(&buf[..n]);
                        if text.starts_with("HTTP/1.1 200") || text.starts_with("HTTP/1.0 200") {
                            return true;
                        }
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// 백엔드 sidecar 실행파일 경로를 계산한다.
///
/// PyInstaller onedir 산출물은 실행파일 하나가 아니라 지원 파일(_internal/)이
/// 딸린 디렉토리라 Tauri의 externalBin(sidecar) 규칙(단일 파일, "<name>-
/// <target-triple>" 명명)에 맞지 않는다. 그래서 tauri.conf.json의
/// bundle.resources로 디렉토리 전체를 번들에 포함시키고 여기서 직접 경로를
/// 계산해 std::process::Command로 실행한다. 개발 모드(`tauri dev`)에서는
/// 번들 리소스가 아직 복사되지 않으므로 컴파일 시점의 CARGO_MANIFEST_DIR
/// 기준 src-tauri/binaries/를 그대로 사용한다.
fn backend_binary_path(app: &tauri::AppHandle) -> PathBuf {
    let exe_name = if cfg!(windows) {
        "easypaper-backend.exe"
    } else {
        "easypaper-backend"
    };
    let base = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries")
    } else {
        app.path()
            .resource_dir()
            .expect("failed to resolve resource dir")
            .join("binaries")
    };
    base.join("easypaper-backend").join(exe_name)
}

fn kill_sidecar(state: &tauri::State<SidecarState>) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            let app_handle = app.handle().clone();

            // 데스크탑 자체 업데이트(git pull이 아니라 Tauri updater로 배선) -
            // tauri.conf.json의 plugins.updater.endpoints는 아직 실제로 존재하지
            // 않는 플레이스홀더 URL이고 pubkey도 이 스파이크에서만 쓰는 임시
            // 키페어라, check()는 지금 성공하지 못하지만(네트워크/서명 오류)
            // 플러그인이 실제로 배선되어 매니페스트 확인 흐름을 시도한다는 것은
            // 검증된다. 실배포 전 진짜 키 생성 + 실제 릴리스 엔드포인트로 교체 필요.
            let updater_handle = app_handle.clone();
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_updater::UpdaterExt;
                match updater_handle.updater() {
                    Ok(updater) => match updater.check().await {
                        Ok(Some(update)) => {
                            log::info!("update available: {}", update.version)
                        }
                        Ok(None) => log::info!("updater check: already up to date"),
                        Err(e) => log::warn!(
                            "updater check failed (expected - placeholder endpoint/key for spike): {}",
                            e
                        ),
                    },
                    Err(e) => log::warn!("updater plugin unavailable: {}", e),
                }
            });
            let port = find_available_port(8000);

            let app_data_dir = app
                .path()
                .app_data_dir()
                .expect("failed to resolve app data dir");
            let uploads_dir = app_data_dir.join("uploads");
            let cache_dir = app_data_dir.join("cache");
            let library_dir = app_data_dir.join("library");
            let logs_dir = app_data_dir.join("logs");
            for dir in [&app_data_dir, &uploads_dir, &cache_dir, &library_dir, &logs_dir] {
                std::fs::create_dir_all(dir).expect("failed to create app data subdirectory");
            }
            let db_path = app_data_dir.join("easypaper.db");

            let binary = backend_binary_path(&app_handle);
            // PyInstaller onedir 산출물은 frontend/dist를 _internal/frontend/dist에
            // 담고 있다(빌드 산출물이 onedir 최상위 밖의 경로를 datas 목적지로
            // 쓸 수 없어서). backend/main.py의 EASYPAPER_FRONTEND_DIST env var로
            // 실제 위치를 알려준다.
            let frontend_dist = binary
                .parent()
                .expect("binary has no parent dir")
                .join("_internal")
                .join("frontend")
                .join("dist");
            log::info!("spawning backend sidecar: {:?} on port {}", binary, port);

            let mut command = Command::new(&binary);
            command
                .env("APP_HOST", "127.0.0.1")
                .env("APP_PORT", port.to_string())
                .env("EASYPAPER_CONFIG_DIR", &app_data_dir)
                .env("DB_PATH", &db_path)
                .env("UPLOAD_DIR", &uploads_dir)
                .env("CACHE_DIR", &cache_dir)
                .env("LIBRARY_DIR", &library_dir)
                .env("EASYPAPER_LOG_DIR", &logs_dir)
                .env("EASYPAPER_FRONTEND_DIST", &frontend_dist)
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());

            #[cfg(windows)]
            command.creation_flags(CREATE_NO_WINDOW);

            let mut child = command.spawn().expect("failed to spawn backend sidecar");

            // 디버깅 편의를 위해 sidecar의 stdout/stderr을 그대로 로그로 흘려보낸다
            // (스파이크 전용 - 실배포 전 로그 볼륨/레벨 조정 필요).
            if let Some(stdout) = child.stdout.take() {
                std::thread::spawn(move || {
                    use std::io::BufRead;
                    for line in std::io::BufReader::new(stdout).lines().flatten() {
                        log::info!("[sidecar] {}", line);
                    }
                });
            }
            if let Some(stderr) = child.stderr.take() {
                std::thread::spawn(move || {
                    use std::io::BufRead;
                    for line in std::io::BufReader::new(stderr).lines().flatten() {
                        log::warn!("[sidecar] {}", line);
                    }
                });
            }

            app.state::<SidecarState>().0.lock().unwrap().replace(child);

            // WindowEvent::CloseRequested/RunEvent::Exit는 GTK/winit 이벤트 루프를
            // 통해서만 발생하고, `kill <pid>`나 시스템 종료가 보내는 SIGTERM은
            // 그 루프를 거치지 않아 두 핸들러 모두 호출되지 않는다(실제로
            // `pkill -f target/debug/app`로 검증됨 - sidecar가 고아로 남았다).
            // 이 경로까지 커버하기 위해 SIGTERM/SIGINT 핸들러를 별도로 등록한다.
            let app_handle_for_signal = app.handle().clone();
            let _ = ctrlc::set_handler(move || {
                log::warn!("received termination signal, killing sidecar");
                kill_sidecar(&app_handle_for_signal.state::<SidecarState>());
                std::process::exit(0);
            });

            let window = app
                .get_webview_window("main")
                .expect("main window not found");
            std::thread::spawn(move || {
                if wait_for_backend(port, Duration::from_secs(15)) {
                    let url = format!("http://127.0.0.1:{port}/");
                    log::info!("backend ready, navigating window to {}", url);
                    if let Err(e) = window.navigate(url.parse().expect("invalid url")) {
                        log::error!("failed to navigate window: {}", e);
                    }
                } else {
                    log::error!("backend healthcheck did not succeed within timeout");
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // Windows에서 X 버튼으로 강제 종료해도 sidecar가 고아 프로세스로
            // 남지 않도록, 창이 실제로 닫히기 전에 명시적으로 kill한다.
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("window close requested, killing sidecar");
                kill_sidecar(&window.state::<SidecarState>());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 창 이벤트를 놓치는 경로(OS 로그아웃/시스템 종료 시그널 등) 대비
            // 앱 종료 시에도 한 번 더 kill을 시도한다.
            if let tauri::RunEvent::Exit = event {
                log::info!("app exiting, ensuring sidecar is killed");
                kill_sidecar(&app_handle.state::<SidecarState>());
            }
        });
}
