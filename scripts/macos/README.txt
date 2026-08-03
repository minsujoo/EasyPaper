Obsidian 전용 Intel Mac 설치 묶음

1. paper-research-obsidian-macos-intel.zip의 압축을 풉니다.
2. install-obsidian-integration.command를 우클릭한 뒤 '열기'를 누릅니다.
3. Finder 창에서 동기화할 Obsidian Vault 폴더를 선택합니다.
4. 필요하면 Tailscale로 받은 paper-sync-connection.env 파일을 선택합니다.
5. Obsidian을 다시 실행하고 커뮤니티 플러그인에서 Paper Research Workspace를 켭니다.

별도 데스크톱 앱은 설치하지 않습니다. 로컬 연구 엔진은 플러그인이 필요할 때만
백그라운드로 실행하고 Obsidian이 종료되면 함께 종료합니다.

Vault 전체 동기화
-----------------
- 노트, 첨부파일, 테마와 플러그인 파일은 중앙 서버를 통해 자동 동기화됩니다.
- 파일 변경 약 5초 뒤와 300초 주기로 동기화합니다.
- 같은 파일을 두 기기에서 동시에 바꾸면 sync-conflict 이름의 사본으로 보존합니다.
- 열린 탭 배치, 캐시, 임시 폴더와 이 플러그인의 기기별 data.json은 동기화하지 않습니다.
