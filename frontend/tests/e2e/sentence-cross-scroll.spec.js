import { test, expect } from '@playwright/test'
import { mockBaseRoutes, gotoApp, SAMPLE_PDF_A } from './helpers.js'

test('문장 클릭은 클릭하지 않은 반대쪽 패널만 스크롤한다', async ({ page }) => {
  const document = {
    id: 'sentence-scroll-doc',
    filename: 'sentence-scroll.pdf',
    total_pages: 1,
    metadata: { title: 'Sentence scroll test' },
    translated_pages: [1],
  }
  await mockBaseRoutes(page, { documents: [document] })
  await page.route('**/api/library/sentence-scroll-doc/pdf', route => route.fulfill({
    status: 200,
    contentType: 'application/pdf',
    body: SAMPLE_PDF_A,
  }))

  const fillerBefore = Array.from({ length: 45 }, (_, i) => `앞쪽 채움 문장 ${i + 1}입니다.`).join(' ')
  const targetTranslation = '원문의 첫 문장에 대응하는 번역 문장입니다.'
  const fillerAfter = Array.from({ length: 45 }, (_, i) => `뒤쪽 채움 문장 ${i + 1}입니다.`).join(' ')
  await page.route('**/api/library/sentence-scroll-doc/translation/1**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      page: 1,
      translation: `${fillerBefore}\n\n${targetTranslation}\n\n${fillerAfter}`,
      sentences: [{ src: 'Sample PDF A - page 1', trans: targetTranslation }],
    }),
  }))
  await page.route('**/api/jobs/sentence-scroll-doc/status', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'completed', total_pages: 1, completed_pages: [1], failed_pages: [] }),
  }))

  await gotoApp(page)
  await page.evaluate(() => { location.hash = '#viewer?id=sentence-scroll-doc' })

  const transSentence = page.locator('.trans-sentence[data-sentence-idx="0"]').first()
  const sourceText = page.locator('.textLayer span').filter({ hasText: 'Sample PDF A - page 1' }).first()
  await expect(transSentence).toBeAttached()
  await expect(sourceText).toBeVisible()

  // 원문 클릭: 원문 열은 그대로이고 번역 열/내부 내용만 이동해야 한다.
  const beforeSourceClick = await page.evaluate(() => ({
    pdf: document.querySelector('#pdf-scroll-column').scrollTop,
    translationColumn: document.querySelector('#translation-scroll-column').scrollTop,
    translation: document.querySelector('#trans-content-1').scrollTop,
  }))
  await sourceText.click()
  await page.waitForTimeout(900)
  const afterSourceClick = await page.evaluate(() => ({
    pdf: document.querySelector('#pdf-scroll-column').scrollTop,
    translationColumn: document.querySelector('#translation-scroll-column').scrollTop,
    translation: document.querySelector('#trans-content-1').scrollTop,
  }))
  expect(Math.abs(afterSourceClick.pdf - beforeSourceClick.pdf)).toBeLessThan(3)
  expect(
    Math.abs(afterSourceClick.translationColumn - beforeSourceClick.translationColumn)
    + Math.abs(afterSourceClick.translation - beforeSourceClick.translation)
  ).toBeGreaterThan(20)

  // 번역 클릭: 원문 열에만 이동 명령이 전달되고 번역 열과 문장은 고정돼야 한다.
  await page.evaluate(() => {
    const viewer = document.querySelector('#pdf-scroll-column')
    // 작은 1페이지 fixture는 실제 스크롤 여유가 없으므로, scrollBy 요청을
    // 기록하는 스파이로 바꿔 PDF 쪽에만 이동 명령이 전달되는지 검증한다.
    viewer.scrollBy = options => {
      viewer.dataset.requestedScrollTop = String(options?.top || 0)
    }
  })
  const beforeTranslationClick = await transSentence.evaluate(el => ({
    top: el.getBoundingClientRect().top,
    pdf: document.querySelector('#pdf-scroll-column').scrollTop,
    translationColumn: document.querySelector('#translation-scroll-column').scrollTop,
    translation: document.querySelector('#trans-content-1').scrollTop,
  }))
  await transSentence.dispatchEvent('click')
  await page.waitForTimeout(1300)
  const afterTranslationClick = await transSentence.evaluate(el => ({
    top: el.getBoundingClientRect().top,
    pdf: document.querySelector('#pdf-scroll-column').scrollTop,
    translationColumn: document.querySelector('#translation-scroll-column').scrollTop,
    translation: document.querySelector('#trans-content-1').scrollTop,
    requested: Number(document.querySelector('#pdf-scroll-column').dataset.requestedScrollTop || 0),
  }))
  expect(Math.abs(afterTranslationClick.requested)).toBeGreaterThan(20)
  expect(afterTranslationClick.pdf).toBe(beforeTranslationClick.pdf)
  expect(afterTranslationClick.translationColumn).toBe(beforeTranslationClick.translationColumn)
  expect(afterTranslationClick.translation).toBe(beforeTranslationClick.translation)
  expect(Math.abs(afterTranslationClick.top - beforeTranslationClick.top)).toBeLessThan(12)
})
