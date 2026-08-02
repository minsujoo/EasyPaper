import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp, SAMPLE_PDF_A } from './helpers.js'

test('히스토리에서 월간 달력과 날짜별 독서 진도를 표시한다', async ({ page }) => {
  const doc = { id: 'reading-doc', filename: 'reading.pdf', total_pages: 10, metadata: { title: 'Calendar Paper', last_page: 4 }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [doc] })
  await page.route('**/api/library/reading-history?*', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      year: 2026,
      month: 8,
      active_days: 1,
      paper_count: 1,
      activities: [{
        doc_id: 'reading-doc', activity_date: '2026-08-01', last_page: 4,
        furthest_page: 6, total_pages: 10, filename: 'reading.pdf',
        metadata: { title: 'Calendar Paper' }, completed: false,
        last_read_at: '2026-08-01T10:00:00Z',
      }],
    }),
  }))

  await gotoApp(page)
  await page.click('#lib-tab-history')
  await expect(page.locator('#library-history-section')).toBeVisible()
  await expect(page.locator('.reading-calendar-paper')).toContainText('Calendar Paper')
  await expect(page.locator('.reading-day-paper')).toContainText('6 / 10페이지')
  await expect(page.locator('#library-stats-container')).toContainText('1')
})

test('논문을 열면 저장 위치를 복원하고 책갈피를 갱신한다', async ({ page }) => {
  const doc = { id: 'bookmark-doc', filename: 'bookmark.pdf', total_pages: 1, metadata: { title: 'Bookmark Paper', last_page: 1 }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [doc] })
  await page.route('**/api/library/bookmark-doc/pdf', route => route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_A }))
  let savedPayload = null
  await page.route('**/api/library/bookmark-doc/reading-progress', async route => {
    savedPayload = route.request().postDataJSON()
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ status: 'success', metadata: { last_page: savedPayload.page, last_read_at: '2026-08-01T10:00:00Z' } }),
    })
  })

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=bookmark-doc' })
  await expect(page.locator('#viewer-screen')).toHaveClass(/active/)
  await expect(page.locator('#viewer-bookmark-btn')).toContainText('1p · 100%')
  await page.locator('#viewer-bookmark-btn').click()
  await expect.poll(() => savedPayload?.page).toBe(1)
})
