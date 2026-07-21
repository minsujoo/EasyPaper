import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

test('로그인 상태에서 라이브러리 화면이 정상적으로 뜬다', async ({ page }) => {
  await mockBaseRoutes(page, { documents: [] })
  await gotoApp(page)

  await expect(page.locator('#library-screen')).toHaveClass(/active/)
  await expect(page.getByText('보관함에 저장된 논문이 없습니다')).toBeVisible()
})

test('라이브러리에 저장된 문서 카드가 표시된다', async ({ page }) => {
  const doc = { id: 'doc-1', filename: 'Paper.pdf', total_pages: 5, metadata: { title: 'A Great Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [doc] })
  await gotoApp(page)

  await expect(page.getByText('A Great Paper')).toBeVisible()
})
