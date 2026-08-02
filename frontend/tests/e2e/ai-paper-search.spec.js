import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

test('자연어 질문으로 외부 논문을 검색하고 AI 관련성을 표시한다', async ({ page }) => {
  await mockBaseRoutes(page)
  await page.route('**/api/paper-search', async route => {
    const request = route.request().postDataJSON()
    expect(request.query).toBe('카메라 기반 4D 점유 예측')
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        question: request.query,
        search_query: 'camera based 4D occupancy forecasting autonomous driving',
        keywords: ['4D occupancy', 'camera-only'],
        answer: '카메라 영상으로 미래 점유 상태를 예측하는 연구들이 검색되었습니다.',
        ai_used: true,
        total: 1,
        source: 'OpenAlex',
        results: Array.from({ length: 15 }, (_, index) => ({
          id: `cam4docc-${index}`, title: index === 0 ? 'Cam4DOcc' : `Related Occupancy Paper ${index + 1}`, year: 2024,
          authors: ['Author One', 'Author Two'], venue: 'CVPR', citation_count: 29,
          url: 'https://doi.org/10.1000/cam4docc', pdf_url: 'https://example.org/cam4docc.pdf',
          is_open_access: true,
          relevance: '카메라만으로 4D 점유 예측을 평가하는 벤치마크를 제안합니다.',
        })),
      }),
    })
  })

  await gotoApp(page)
  await page.click('#lib-tab-paper-search')
  await page.click('.scholar-mode-btn[data-mode="search"]')
  await page.fill('#paper-search-input', '카메라 기반 4D 점유 예측')
  await page.click('#paper-search-submit')

  await expect(page.locator('#paper-search-answer-text')).toContainText('미래 점유 상태')
  await expect(page.locator('.paper-search-card').first()).toContainText('Cam4DOcc')
  await expect(page.locator('.paper-search-card').first()).toContainText('왜 관련 있나요?')
  await expect(page.locator('.paper-search-card-actions').first()).toContainText('PDF 열기')
  await expect(page.locator('.paper-search-card-actions').first()).toContainText('라이브러리에 저장')
  await expect(page.locator('.paper-search-card-actions').first()).toContainText('유사 논문')
  await expect(page.locator('.paper-search-card-actions').first()).toContainText('BibTeX')
  const scroll = await page.locator('#paper-search-section').evaluate(element => {
    const before = element.scrollTop
    element.scrollTop = element.scrollHeight
    return { before, after: element.scrollTop, height: element.scrollHeight, viewport: element.clientHeight }
  })
  expect(scroll.height).toBeGreaterThan(scroll.viewport)
  expect(scroll.after).toBeGreaterThan(scroll.before)
})

test('Scholar 맞춤 추천에서 평가하고 공개 PDF를 라이브러리에 저장한다', async ({ page }) => {
  await mockBaseRoutes(page)
  await page.route('**/api/library/folders', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ folders: [] }),
  }))
  await page.route('**/api/scholar/feed**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      answer: '보관함을 기준으로 맞춤 논문 1편을 골랐습니다.',
      total: 1, source: 'OpenAlex', ai_used: true, keywords: [], search_queries: [],
      results: [{
        id: 'gaussian-world', title: 'GaussianWorld', year: 2025,
        authors: ['Author One'], venue: 'ICCV', citation_count: 8,
        url: 'https://example.org/paper', pdf_url: 'https://example.org/paper.pdf',
        is_open_access: true, relevance: '3D occupancy prediction에 Gaussian 표현을 사용합니다.', rating: 0,
      }],
    }),
  }))
  let savedRating = null
  await page.route('**/api/scholar/feedback', async route => {
    savedRating = route.request().postDataJSON().rating
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ paper_id: 'gaussian-world', rating: savedRating }),
    })
  })
  await page.route('**/api/scholar/import', route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ session_id: 'saved', filename: 'GaussianWorld.pdf', total_pages: 10, metadata: {}, saved: true }),
  }))

  await gotoApp(page)
  await page.click('#lib-tab-paper-search')
  await expect(page.locator('#paper-search-result-title')).toHaveText('맞춤 추천')
  await expect(page.locator('.paper-search-card').first()).toContainText('GaussianWorld')

  await page.click('.paper-search-card .scholar-rating-btn[data-rating="1"]')
  expect(savedRating).toBe(1)
  await expect(page.locator('.paper-search-card .scholar-rating-btn[data-rating="1"]')).toHaveClass(/active/)

  await page.click('.paper-search-card .scholar-save-btn')
  await expect(page.locator('.paper-search-card .scholar-save-btn')).toHaveText('저장됨 ✓')
})
