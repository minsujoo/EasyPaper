import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp, SAMPLE_PDF_A } from './helpers.js'

// AI 채팅 답변에서 텍스트를 선택하면 "Ask AI"로 후속 질문을 인용할 수 있어야 한다
// (PDF/번역본 인용 기능을 채팅 답변에도 확장한 기능의 회귀 테스트)
test('AI 채팅 답변 텍스트를 선택하면 Ask AI로 인용할 수 있다', async ({ page }) => {
  const docA = { id: 'doc-A', filename: 'DocA.pdf', total_pages: 1, metadata: { title: 'Document A' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docA] })

  await page.route('**/api/library/doc-A/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_A }))
  await page.route('**/api/chat/stream', route =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: '트랜스포머 아키텍처는 셀프 어텐션 메커니즘을 핵심으로 사용하는 딥러닝 모델입니다.' }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-A' })
  await page.waitForTimeout(1200)

  await page.click('#chat-toggle-btn')
  await page.fill('#chat-input', '트랜스포머 아키텍처가 뭐야?')
  await page.click('#chat-send-btn')
  await page.waitForTimeout(800)

  const bubbleSelector = '.chat-message.assistant:not(.temp-typing) .message-bubble'
  await page.waitForSelector(bubbleSelector)

  const selection = await page.evaluate((sel) => {
    const bubbles = document.querySelectorAll(sel)
    const bubble = bubbles[bubbles.length - 1]
    const text = bubble.textContent
    const target = '셀프 어텐션'
    const start = text.indexOf(target)
    if (start === -1) return null

    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT)
    let node, offset = 0, startNode = null, startOffset = 0, endNode = null, endOffset = 0
    while ((node = walker.nextNode())) {
      const len = node.textContent.length
      if (startNode === null && start >= offset && start < offset + len) {
        startNode = node; startOffset = start - offset
      }
      const end = start + target.length
      if (end >= offset && end <= offset + len) {
        endNode = node; endOffset = end - offset
      }
      offset += len
    }
    if (!startNode || !endNode) return null

    const sel_ = window.getSelection()
    sel_.removeAllRanges()
    const r = document.createRange()
    r.setStart(startNode, startOffset)
    r.setEnd(endNode, endOffset)
    sel_.addRange(r)
    const rect = r.getBoundingClientRect()
    return { text: sel_.toString(), rect: { x: rect.left, y: rect.top } }
  }, bubbleSelector)

  expect(selection).not.toBeNull()
  expect(selection.text).toBe('셀프 어텐션')

  await page.mouse.move(selection.rect.x + 5, selection.rect.y + 5)
  await page.mouse.up()

  await expect(page.locator('.selection-menu .ask-ai-btn')).toBeVisible()

  await page.click('.selection-menu .ask-ai-btn')

  await expect(page.locator('#chat-quote-area')).not.toHaveClass(/hidden/)
  await expect(page.locator('#chat-quote-text')).toHaveText('셀프 어텐션')
})

// 사용자 자신이 보낸 메시지는 인용 대상에서 제외되어야 한다
test('사용자 자신의 채팅 메시지는 Ask AI 인용 대상이 아니다', async ({ page }) => {
  const docA = { id: 'doc-A', filename: 'DocA.pdf', total_pages: 1, metadata: { title: 'Document A' }, translated_pages: [] }
  await mockBaseRoutes(page, { documents: [docA] })
  await page.route('**/api/library/doc-A/pdf', route =>
    route.fulfill({ status: 200, contentType: 'application/pdf', body: SAMPLE_PDF_A }))
  await page.route('**/api/chat/stream', route =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: '답변입니다.' }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=doc-A' })
  await page.waitForTimeout(1200)
  await page.click('#chat-toggle-btn')
  await page.fill('#chat-input', '이것은 사용자 질문 텍스트')
  await page.click('#chat-send-btn')
  await page.waitForTimeout(500)

  const userBubbleFound = await page.evaluate(() => {
    const bubble = document.querySelector('.chat-message.user .message-bubble')
    if (!bubble) return false
    const range = document.createRange()
    range.selectNodeContents(bubble)
    window.getSelection().removeAllRanges()
    window.getSelection().addRange(range)
    return true
  })
  expect(userBubbleFound).toBe(true)

  await page.mouse.move(200, 200)
  await page.mouse.up()
  await page.waitForTimeout(300)

  // 사용자 메시지 선택으로는 Ask AI 메뉴가 뜨지 않아야 한다
  const menuHidden = await page.evaluate(() => {
    const menu = document.querySelector('.selection-menu')
    return !menu || menu.classList.contains('hidden')
  })
  expect(menuHidden).toBe(true)
})
