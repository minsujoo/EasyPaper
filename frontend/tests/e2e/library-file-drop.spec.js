import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

test('파일 관리자에서 끌어온 PDF를 보관함에 놓으면 업로드한다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  let uploadedBody = ''
  await page.route('**/api/upload*', async route => {
    uploadedBody = route.request().postData() || ''
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        session_id: 'dropped-doc',
        filename: 'dragged-paper.pdf',
        total_pages: 1,
        metadata: { title: 'Dragged Paper' },
      }),
    })
  })
  await gotoApp(page)

  await page.evaluate(() => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['%PDF-1.4 test'], 'dragged-paper.pdf', { type: 'application/pdf' }))
    const target = document.querySelector('#library-screen')
    target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }))
    target.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }))
  })

  await expect.poll(() => uploadedBody).toContain('dragged-paper.pdf')
  await expect(page.locator('#upload-popup-title')).toContainText('업로드 완료')
  await expect(page.getByText('1개의 논문이 라이브러리에 추가되었습니다')).toBeVisible()
})
