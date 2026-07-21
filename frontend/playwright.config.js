import { defineConfig, devices } from '@playwright/test'

// EasyPaper E2E 테스트 설정
// 실제 백엔드에 의존하지 않고, 각 테스트가 page.route()로 /api/** 응답을
// 모킹해 프론트엔드 로직만 독립적으로 검증한다 (dist 빌드를 정적 서빙).
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['html', { open: 'never' }], ['list']],
  use: {
    baseURL: 'http://localhost:8934',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'python3 -m http.server 8934 --directory dist',
    url: 'http://localhost:8934',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
