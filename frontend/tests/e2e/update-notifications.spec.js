import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

async function mockUpdateEndpoints(page, { postUpdateShow, updateAvailable }) {
  await page.route('**/api/settings/update-check-config', async route => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON()
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ interval: body.interval, last_checked_at: null }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ interval: 'weekly', last_checked_at: null }) })
  })
  await page.route('**/api/settings/update-check', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      ok: true, current_version: 'abc1234', current_version_date: '2026-07-15',
      latest_version: 'def5678', latest_version_date: '2026-07-21', update_available: updateAvailable,
      changelog: updateAvailable ? [
        { sha: 'def5678', subject: 'feat: 새 기능 추가' },
        { sha: 'aaa1111', subject: 'fix: 버그 수정' },
      ] : [],
    }),
  }))
  await page.route('**/api/settings/post-update-notice', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      show: postUpdateShow, version: 'abc1234', version_date: '2026-07-15',
      changelog: postUpdateShow ? [{ sha: 'aaa', subject: 'fix: 세션 토큰 서명 키 취약점 수정' }] : [],
    }),
  }))
}

test('방금 업데이트된 직후에는 완료 안내만 뜨고, 새 업데이트 확인 팝업과 겹치지 않는다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await mockUpdateEndpoints(page, { postUpdateShow: true, updateAvailable: true })
  await gotoApp(page)
  await page.waitForTimeout(1000)

  await expect(page.locator('#update-complete-modal')).not.toHaveClass(/hidden/)
  await expect(page.locator('#update-available-modal')).toHaveClass(/hidden/)
  await expect(page.locator('#update-complete-version-line')).toHaveText('버전 2026-07-15 · abc1234로 업데이트되었습니다.')

  await page.click('#update-complete-confirm-btn')
  await expect(page.locator('#update-complete-modal')).toHaveClass(/hidden/)
})

test('방금 업데이트되지 않았고 새 업데이트가 있으면 변경 로그 팝업이 뜬다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await mockUpdateEndpoints(page, { postUpdateShow: false, updateAvailable: true })
  await gotoApp(page)
  await page.waitForTimeout(1000)

  await expect(page.locator('#update-complete-modal')).toHaveClass(/hidden/)
  await expect(page.locator('#update-available-modal')).not.toHaveClass(/hidden/)
  await expect(page.locator('#update-available-version-line')).toHaveText('2026-07-15 · abc1234 → 2026-07-21 · def5678')
  await expect(page.locator('#update-available-changelog')).toContainText('feat: 새 기능 추가')
  await expect(page.locator('#update-available-changelog')).toContainText('fix: 버그 수정')

  await page.click('#update-available-later-btn')
  await expect(page.locator('#update-available-modal')).toHaveClass(/hidden/)
})

test('업데이트도 없고 방금 업데이트되지도 않았으면 아무 팝업도 뜨지 않는다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await mockUpdateEndpoints(page, { postUpdateShow: false, updateAvailable: false })
  await gotoApp(page)
  await page.waitForTimeout(1000)

  await expect(page.locator('#update-complete-modal')).toHaveClass(/hidden/)
  await expect(page.locator('#update-available-modal')).toHaveClass(/hidden/)
})

test('설정 화면에서 업데이트 확인 주기를 변경하면 저장된다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await mockUpdateEndpoints(page, { postUpdateShow: false, updateAvailable: false })

  let savedInterval = null
  await page.route('**/api/settings/update-check-config', async route => {
    if (route.request().method() === 'POST') {
      savedInterval = route.request().postDataJSON().interval
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ interval: savedInterval, last_checked_at: null }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ interval: 'weekly', last_checked_at: null }) })
  })

  await gotoApp(page)
  await page.waitForTimeout(600)
  await page.click('#global-settings-btn')
  await page.waitForTimeout(400)

  await expect(page.locator('#setting-update-check-interval')).toHaveValue('weekly')
  await page.selectOption('#setting-update-check-interval', 'never')
  await page.waitForTimeout(300)

  expect(savedInterval).toBe('never')
})
