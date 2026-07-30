import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp } from './helpers.js'

test('홈 노트 탭에서 자동 생성된 논문 노트를 열 수 있다', async ({ page }) => {
  await mockBaseRoutes(page)

  const content = {
    title: 'Cam4DOcc',
    one_line_summary: '카메라만으로 미래 4차원 점유 상태를 예측합니다.',
    summary: '새 벤치마크와 예측 네트워크를 제안하고 기준선과 비교합니다.',
    contributions: ['4차원 점유 예측 벤치마크를 제안합니다.'],
    method_summary: '다중 카메라 특징을 시공간으로 집계합니다.',
    results_summary: '제안 모델이 기준선보다 우수했습니다.',
    limitations: '긴 시간 예측은 여전히 어렵습니다.',
    takeaways: ['시공간 정보의 공동 모델링이 중요합니다.'],
    keywords: ['4D occupancy', 'autonomous driving'],
    experiment_flow: [],
    visuals: [],
  }
  await page.route('**/api/notes', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      notes: [{
        doc_id: 'cam4docc',
        filename: 'cam4docc.pdf',
        total_pages: 13,
        metadata: { title: 'Cam4DOcc' },
        status: 'ready',
        content,
      }],
    }),
  }))
  await page.route('**/api/notes/cam4docc', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ doc_id: 'cam4docc', status: 'ready', content }),
  }))

  await gotoApp(page)
  await page.click('#lib-tab-notes')

  await expect(page.locator('.paper-note-card')).toContainText('Cam4DOcc')
  await expect(page.locator('.paper-note-card')).toContainText('완료')
  await page.click('.paper-note-card')

  await expect(page.locator('#paper-note-modal')).toHaveClass(/is-visible/)
  await expect(page.locator('#paper-note-modal-body')).toContainText('논문 요약')
  await expect(page.locator('#paper-note-modal-body')).toContainText('긴 시간 예측은 여전히 어렵습니다.')
})
