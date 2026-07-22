import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

// 라이브러리 전체 검색: 파일명/제목/카테고리 + 번역된 본문 텍스트를 가로지르는 검색
test('검색어 입력 시 서버 검색 결과가 표시되고, 지우면 원래 목록으로 돌아간다', async ({ page }) => {
  const docs = [
    { id: 'doc-1', filename: 'transformer.pdf', total_pages: 3, metadata: { title: 'Attention Is All You Need' }, translated_pages: [] },
    { id: 'doc-2', filename: 'gan.pdf', total_pages: 2, metadata: { title: 'GAN Paper' }, translated_pages: [] },
  ]
  await mockBaseRoutes(page, { documents: docs })

  let searchQuery = null
  await page.route('**/api/library/search*', route => {
    const url = new URL(route.request().url())
    searchQuery = url.searchParams.get('q')
    const matched = docs.filter(d => d.metadata.title.toLowerCase().includes(searchQuery.toLowerCase()))
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ documents: matched, total: matched.length }) })
  })

  await gotoApp(page)
  await expect(page.getByText('Attention Is All You Need')).toBeVisible()
  await expect(page.getByText('GAN Paper')).toBeVisible()

  await page.fill('#library-search-input', 'Attention')
  await page.waitForTimeout(500) // 디바운스 대기

  expect(searchQuery).toBe('Attention')
  await expect(page.getByText('Attention Is All You Need')).toBeVisible()
  await expect(page.getByText('GAN Paper')).not.toBeVisible()
  await expect(page.locator('#library-search-status')).toContainText('검색 결과 1건')

  // 검색 지우기 -> 원래 전체 목록으로 복귀
  await page.click('#library-search-clear-btn')
  await page.waitForTimeout(300)
  await expect(page.getByText('Attention Is All You Need')).toBeVisible()
  await expect(page.getByText('GAN Paper')).toBeVisible()
  await expect(page.locator('#library-search-status')).toHaveClass(/hidden/)
})

test('검색 결과가 없으면 안내 메시지가 표시된다', async ({ page }) => {
  const docs = [
    { id: 'doc-1', filename: 'paper.pdf', total_pages: 1, metadata: { title: 'Some Paper' }, translated_pages: [] },
  ]
  await mockBaseRoutes(page, { documents: docs })
  await page.route('**/api/library/search*', route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ documents: [], total: 0 }) }))

  await gotoApp(page)
  await page.fill('#library-search-input', '존재하지않는검색어')
  await page.waitForTimeout(500)

  await expect(page.getByText('검색 결과가 없습니다')).toBeVisible()
  await expect(page.locator('#library-search-status')).toContainText('검색 결과 0건')
})
