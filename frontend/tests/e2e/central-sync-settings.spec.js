import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'


test('중앙 동기화 서버를 설정하고 즉시 동기화한다', async ({ page }) => {
  await mockBaseRoutes(page)
  let saved = null
  await page.route('**/api/settings/sync', async route => {
    if (route.request().method() === 'POST') {
      saved = route.request().postDataJSON()
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          server_url: saved.server_url, token_set: true,
          interval_seconds: saved.interval_seconds,
          runtime: {
            server_url: saved.server_url, token_set: true, device_id: 'device-1234',
            running: false, last_completed_at: null, last_error: null,
          },
        }),
      })
    }
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        server_url: '', token_set: false, interval_seconds: 300,
        runtime: { server_url: '', token_set: false, running: false },
      }),
    })
  })
  await page.route('**/api/sync/run', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ pushed: 12, pulled: 4, uploaded: 1, downloaded: 0, conflicts: 0 }),
  }))

  await gotoApp(page)
  await page.click('#global-settings-btn')
  await page.click('.tab-btn[data-tab="tab-sync"]')
  await expect(page.locator('#tab-sync')).toHaveClass(/active/)

  await page.fill('#setting-sync-server-url', 'https://sync.example.com')
  await page.fill('#setting-sync-token', 'private-device-token')
  await page.selectOption('#setting-sync-interval', '60')
  await page.click('#setting-sync-save-btn')

  expect(saved).toEqual({
    server_url: 'https://sync.example.com',
    token: 'private-device-token',
    interval_seconds: 60,
  })
  await expect(page.locator('#setting-sync-status')).toContainText('연결 준비됨')
  await page.click('#setting-sync-run-btn')
  await expect(page.locator('.toast')).toContainText('보냄 12, 받음 4')
})
