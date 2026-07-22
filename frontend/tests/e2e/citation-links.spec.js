import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp, SAMPLE_PDF_CITATION } from './helpers.js'

test('참고문헌 목록에 있는 번호의 본문 인용 표기만 클릭 가능한 오버레이가 생기고, 클릭하면 외부 링크가 새 탭으로 열린다', async ({ page, context }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  // [2]는 참고문헌 목록에서 의도적으로 제외 - 목록에 없는 번호는 클릭 불가능해야 한다
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route('**/api/library/doc-C/references/1', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ title: 'Attention Is All You Need', url: 'https://arxiv.org/abs/1706.03762', year: 2017 }),
    }))
  await context.route('https://arxiv.org/**', route =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html></html>' }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  const markerBoxes = page.locator('.citation-marker-box')
  await expect(markerBoxes).toHaveCount(1)
  await expect(markerBoxes.first()).toHaveAttribute('data-ref-num', '1')

  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    markerBoxes.first().click(),
  ])
  await popup.waitForLoadState('domcontentloaded')
  expect(popup.url()).toBe('https://arxiv.org/abs/1706.03762')
})

test('참고문헌을 찾지 못하면 오류 토스트를 표시한다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route('**/api/library/doc-C/references/1', route =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: '외부에서 일치하는 논문을 찾지 못했습니다.' }) }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  await page.locator('.citation-marker-box').first().click()
  await expect(page.locator('.toast')).toHaveClass(/show/)
  await expect(page.locator('.toast')).toHaveText('참고문헌을 찾지 못했습니다.')
})
