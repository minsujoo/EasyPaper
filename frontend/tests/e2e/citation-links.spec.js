import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp, SAMPLE_PDF_CITATION } from './helpers.js'

test('본문 인용 카드에 인용 이유·논문 개요와 PDF 다운로드 버튼이 뜬다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' },
        mentions: {
          '1': {
            titles: ['This work builds on the Transformer architecture'],
            authors: ['Vaswani'],
          },
        },
      }),
    }))
  await page.route(/\/api\/library\/doc-C\/references\/1(?:\?refresh=true)?$/, route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        title: 'Attention Is All You Need',
        url: 'https://arxiv.org/abs/1706.03762',
        pdf_url: 'https://arxiv.org/pdf/1706.03762.pdf',
        year: 2017,
        authors: ['Ashish Vaswani', 'Noam Shazeer'],
        venue: 'NeurIPS',
        abstract: 'A sequence transduction model based entirely on attention.',
        citation_count: 120000,
      }),
    }))
  await page.route('**/api/library/doc-C/references/1/insight', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        content: '## 이 논문이 인용된 이유\nTransformer 구조의 배경 근거로 인용했습니다.\n\n## 인용 논문 개요\nSelf-attention 기반 모델을 제안한 논문입니다.',
      }),
    }))
  await page.route('**/api/library/doc-C/references/1/download', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/pdf',
      headers: { 'Content-Disposition': 'attachment; filename="attention.pdf"' },
      body: Buffer.from('%PDF-1.7 citation paper'),
    }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })

  const titleMarker = page.locator('.citation-marker-box[data-mention-kind="title"]').first()
  await expect(titleMarker).toBeVisible()
  await titleMarker.click()

  await expect(page.locator('.citation-paper-title')).toContainText('Attention Is All You Need (2017)')
  await expect(page.locator('.citation-paper-authors')).toContainText('Ashish Vaswani')
  await expect(page.locator('.citation-paper-insight')).toContainText('이 논문이 인용된 이유')
  await expect(page.locator('.citation-paper-insight')).toContainText('배경 근거로 인용')
  await expect(page.locator('.citation-paper-insight')).toContainText('인용 논문 개요')
  await expect(page.locator('.citation-paper-insight')).toContainText('Self-attention 기반 모델')

  const downloadPromise = page.waitForEvent('download')
  await page.locator('.citation-tooltip-download-btn').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('attention.pdf')
})

test('설명을 누르면 자동 프롬프트를 숨긴 독립 팝업에서 후속 채팅을 이어간다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  const chatRequests = []
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route(/\/api\/library\/doc-C\/references\/1(?:\?refresh=true)?$/, route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        title: 'Attention Is All You Need',
        url: 'https://arxiv.org/abs/1706.03762',
        year: 2017,
        authors: ['Ashish Vaswani'],
        abstract: 'A sequence transduction model based entirely on attention.',
      }),
    }))
  await page.route('**/api/chat/stream', async route => {
    chatRequests.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'text/plain', body: '이 인용은 선행 방법의 배경 근거로 사용됩니다.' })
  })

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })

  await page.locator('.citation-marker-box').first().click()
  await expect(page.locator('.citation-tooltip-explain-btn')).toBeVisible()
  await page.locator('.citation-tooltip-explain-btn').click()

  const explanation = page.locator('.explanation-popup').first()
  await expect(explanation).toBeVisible()
  await expect(explanation.locator('.explanation-popup-title')).toHaveText('설명')
  await expect(explanation.locator('.explanation-popup-target')).toContainText('참고문헌 1')
  await expect(explanation.locator('.explanation-popup-message.user')).toHaveCount(0)
  await expect(explanation.locator('.explanation-popup-message.assistant')).toContainText('이 인용은 선행 방법의 배경 근거')
  await expect(page.locator('#chat-sidebar')).toHaveClass(/hidden/)
  await expect.poll(() => chatRequests.length).toBe(1)
  expect(chatRequests[0].hidden_user_message).toBe(true)
  expect(chatRequests[0].chat_session_id).toMatch(/^explain:doc-C:/)
  expect(chatRequests[0].messages.at(-1).content).toContain('Vaswani et al. Attention Is All You Need')
  expect(chatRequests[0].messages.at(-1).content).toContain('왜 언급되었는지 설명해줘')

  await explanation.locator('textarea').fill('이 방법과 현재 논문의 차이는 뭐야?')
  await explanation.locator('.explanation-popup-form button').click()
  await expect.poll(() => chatRequests.length).toBe(2)
  expect(chatRequests[1].hidden_user_message).toBe(false)
  expect(chatRequests[1].chat_session_id).toBe(chatRequests[0].chat_session_id)
  expect(chatRequests[1].messages.at(-1).content).toContain('[설명 대상: 인용')
  expect(chatRequests[1].messages.at(-1).content).toContain('이 방법과 현재 논문의 차이는 뭐야?')
  await expect(explanation.locator('.explanation-popup-message.user')).toContainText('이 방법과 현재 논문의 차이는 뭐야?')
})

test('PDF 본문의 설명 아이콘은 제목 옆에 있고 팝업 동안 숨겨지며 다시 열면 새 세션을 만든다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  const chatRequests = []
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route('**/api/chat/stream', async route => {
    chatRequests.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'text/plain', body: '이 섹션은 참고문헌 목록을 정리합니다.' })
  })

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })

  const sectionButton = page.locator('.section-explain-btn[title*="References"]').first()
  await expect(sectionButton).toBeVisible()
  await expect(sectionButton).toHaveText('')
  await expect(sectionButton).toHaveAttribute('aria-label', /References 설명/)
  const sectionButtonBox = await sectionButton.boundingBox()
  const pdfPageBox = await page.locator('.pdf-page-wrapper[data-page="1"]').boundingBox()
  expect(sectionButtonBox.x).toBeGreaterThanOrEqual(pdfPageBox.x)
  expect(sectionButtonBox.x + sectionButtonBox.width).toBeLessThanOrEqual(pdfPageBox.x + pdfPageBox.width)
  await sectionButton.click()

  const firstPopup = page.locator('.explanation-popup').first()
  await expect(firstPopup).toBeVisible()
  await expect(sectionButton).toBeHidden()
  await expect(firstPopup.locator('.explanation-popup-target')).toContainText('References')
  await expect(page.locator('.explanation-connector-svg path')).toHaveCount(1)
  await expect.poll(() => chatRequests.length).toBe(1)
  expect(chatRequests[0].hidden_user_message).toBe(true)
  expect(chatRequests[0].messages.at(-1).content).toContain('[섹션 제목]')
  expect(chatRequests[0].messages.at(-1).content).toContain('References')
  expect(chatRequests[0].messages.at(-1).content).toContain('논리 전개')

  const before = await firstPopup.boundingBox()
  expect(before.width).toBeGreaterThanOrEqual(470)
  expect(before.height).toBeGreaterThanOrEqual(500)

  const resizeBox = await firstPopup.locator('.explanation-popup-resize-handle').boundingBox()
  await page.mouse.move(resizeBox.x + resizeBox.width / 2, resizeBox.y + resizeBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(resizeBox.x + 90, resizeBox.y + 65, { steps: 6 })
  await page.mouse.up()
  const resized = await firstPopup.boundingBox()
  expect(resized.width).toBeGreaterThan(before.width)
  expect(resized.height).toBeGreaterThan(before.height)
  await expect(page.locator('.explanation-connector-svg path')).toHaveCount(1)

  const headerBox = await firstPopup.locator('.explanation-popup-header').boundingBox()
  await page.mouse.move(headerBox.x + 30, headerBox.y + 18)
  await page.mouse.down()
  await page.mouse.move(headerBox.x + 110, headerBox.y + 68, { steps: 6 })
  await page.mouse.up()
  const after = await firstPopup.boundingBox()
  expect(after.x).not.toBe(resized.x)

  await firstPopup.locator('.explanation-popup-close').click()
  await expect(firstPopup).toHaveCount(0)
  await expect(sectionButton).toBeVisible()
  await sectionButton.click()
  await expect(page.locator('.explanation-popup')).toHaveCount(1)
  await expect(sectionButton).toBeHidden()
  await expect.poll(() => chatRequests.length).toBe(2)
  expect(chatRequests[1].chat_session_id).not.toBe(chatRequests[0].chat_session_id)
})

test('그림과 표의 설명 버튼은 캡처 모드가 아니어도 보이고 이미지 설명 팝업을 연다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  const chatRequests = []
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/images', route =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        images: [
          { page: 1, left: 8, top: 12, width: 38, height: 20, label: 'Table 1', caption: 'Accuracy comparison' },
          { page: 1, left: 53, top: 48, width: 35, height: 24, label: 'Figure 2', caption: 'Model overview' },
        ],
      }),
    }))
  await page.route('**/api/chat/stream', async route => {
    chatRequests.push(route.request().postDataJSON())
    await route.fulfill({ status: 200, contentType: 'text/plain', body: '표의 주요 결과를 설명합니다.' })
  })

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })

  await expect(page.locator('#viewer-scroll-container')).not.toHaveClass(/crop-mode/)
  const imageButtons = page.locator('.pdf-figure-explain-btn')
  await expect(imageButtons).toHaveCount(2)
  await expect(imageButtons.first()).toBeVisible()
  await expect(imageButtons.first()).toHaveText('')
  await expect(imageButtons.first()).toHaveAttribute('aria-label', 'Table 1 설명')
  const imageButtonBox = await imageButtons.first().boundingBox()
  const pdfPageBox = await page.locator('.pdf-page-wrapper[data-page="1"]').boundingBox()
  expect(imageButtonBox.x).toBeGreaterThanOrEqual(pdfPageBox.x)
  expect(imageButtonBox.x + imageButtonBox.width).toBeLessThanOrEqual(pdfPageBox.x + pdfPageBox.width)
  await imageButtons.first().click()

  const popup = page.locator('.explanation-popup').first()
  await expect(popup).toBeVisible()
  await expect(imageButtons.first()).toBeHidden()
  await expect(popup.locator('.explanation-popup-target')).toContainText('Table 1')
  await expect(popup.locator('.explanation-popup-image')).toBeVisible()
  await expect.poll(() => chatRequests.length).toBe(1)
  expect(chatRequests[0].hidden_user_message).toBe(true)
  expect(chatRequests[0].image_base64).toBeTruthy()
  expect(chatRequests[0].messages.at(-1).content).toContain('행과 열 및 지표')
})

test('참고문헌 목록에 있는 번호의 본문 인용 표기만 클릭 가능한 오버레이가 생기고, 클릭하면 원문 텍스트와 함께 툴팁이 뜬다', async ({ page }) => {
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

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  const markerBoxes = page.locator('.citation-marker-box')
  await expect(markerBoxes).toHaveCount(1)
  await expect(markerBoxes.first()).toHaveAttribute('data-ref-num', '1')

  await markerBoxes.first().click()
  await expect(page.locator('.citation-tooltip')).not.toHaveClass(/hidden/)
  await expect(page.locator('.citation-tooltip-text')).toHaveText('Vaswani et al. Attention Is All You Need. 2017.')
})

test('툴팁에서 원문 링크 찾기를 누르면 결과 링크가 표시되고, 클릭하면 새 탭으로 열린다', async ({ page, context }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route(/\/api\/library\/doc-C\/references\/1(?:\?refresh=true)?$/, route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ title: 'Attention Is All You Need', url: 'https://arxiv.org/abs/1706.03762', year: 2017 }),
    }))
  await context.route('https://arxiv.org/**', route =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html></html>' }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  await page.locator('.citation-marker-box').first().click()
  await page.click('.citation-tooltip-resolve-btn')
  await expect(page.locator('.citation-tooltip-result a')).toContainText('Attention Is All You Need (2017)')

  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.click('.citation-tooltip-result a'),
  ])
  await popup.waitForLoadState('domcontentloaded')
  expect(popup.url()).toBe('https://arxiv.org/abs/1706.03762')
})

test('원문 링크를 찾지 못하면 안내 문구가 뜨고, Google Scholar 검색 버튼으로 대체 검색을 할 수 있다', async ({ page, context }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))
  await page.route(/\/api\/library\/doc-C\/references\/1(?:\?refresh=true)?$/, route =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: '외부에서 일치하는 논문을 찾지 못했습니다.' }) }))
  await context.route('https://scholar.google.com/**', route =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html></html>' }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  await page.locator('.citation-marker-box').first().click()
  await page.click('.citation-tooltip-resolve-btn')
  await expect(page.locator('.citation-tooltip-result')).toHaveText('원문 링크를 찾지 못했습니다. Google Scholar 검색을 이용해보세요.')

  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.click('.citation-tooltip-scholar-btn'),
  ])
  await popup.waitForLoadState('domcontentloaded')
  expect(decodeURIComponent(popup.url())).toBe('https://scholar.google.com/scholar?q=Vaswani et al. Attention Is All You Need. 2017.')
})

test('인용 표기가 아닌 다른 곳을 클릭하면(예: 스크롤) 열려 있던 툴팁이 닫힌다', async ({ page }) => {
  const docC = { id: 'doc-C', filename: 'Citation.pdf', total_pages: 1, metadata: { title: 'Citation Sample Paper' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docC] })
  await page.route('**/api/library/doc-C/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_CITATION }))
  await page.route('**/api/library/doc-C/references', route =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ references: { '1': 'Vaswani et al. Attention Is All You Need. 2017.' } }),
    }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-C' })
  await page.waitForTimeout(1500)

  await page.locator('.citation-marker-box').first().click()
  await expect(page.locator('.citation-tooltip')).not.toHaveClass(/hidden/)

  await page.evaluate(() => document.querySelector('#viewer-scroll-container')?.dispatchEvent(new Event('scroll', { bubbles: true })))
  await expect(page.locator('.citation-tooltip')).toHaveClass(/hidden/)
})
