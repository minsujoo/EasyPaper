import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

test('폴더 필터로 논문을 분류해서 보고 다른 폴더로 이동한다', async ({ page }) => {
  const docs = [
    { id: 'doc-a', filename: 'a.pdf', total_pages: 2, folder_id: 1, metadata: { title: 'Occupancy Paper' }, translated_pages: [] },
    { id: 'doc-b', filename: 'b.pdf', total_pages: 3, folder_id: null, metadata: { title: 'Planning Paper' }, translated_pages: [] },
  ]
  const folders = [
    { id: 1, name: '3D Occupancy', document_count: 1 },
    { id: 2, name: 'Planning', document_count: 0 },
  ]
  await mockBaseRoutes(page, { documents: docs })
  await page.route('**/api/library/folders', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders, total: folders.length }) }))
  await page.route('**/api/library/doc-a/folder', async route => {
    const payload = route.request().postDataJSON()
    docs[0].folder_id = payload.folder_id
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', folder_id: payload.folder_id }) })
  })

  await gotoApp(page)
  await expect(page.getByText('Occupancy Paper')).toBeVisible()
  await expect(page.getByText('Planning Paper')).toBeVisible()

  await page.locator('.library-folder-chip', { hasText: '3D Occupancy' }).click()
  await expect(page.getByText('Occupancy Paper')).toBeVisible()
  await expect(page.getByText('Planning Paper')).not.toBeVisible()

  await page.locator('[data-id="doc-a"] .doc-folder-btn').click()
  await page.locator('.library-folder-move-popup button', { hasText: 'Planning' }).click()
  await expect(page.getByText('Occupancy Paper')).not.toBeVisible()

  await page.locator('.library-folder-chip', { hasText: 'Planning' }).click()
  await expect(page.getByText('Occupancy Paper')).toBeVisible()
})

test('새 폴더를 만들면 해당 폴더가 선택된다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await page.route('**/api/library/folders', async route => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 7, name: '새 연구', document_count: 0 }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [{ id: 7, name: '새 연구', document_count: 0 }], total: 1 }) })
  })

  await gotoApp(page)
  await page.click('#library-folder-create-btn')
  await page.fill('.custom-prompt-input', '새 연구')
  await page.locator('.custom-confirm-modal .confirm-btn').click()
  await expect(page.locator('.library-folder-chip-wrap.active')).toContainText('새 연구')
})
