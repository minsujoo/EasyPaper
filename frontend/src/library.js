const API_BASE = '/api'

function buildQuery(options) {
  if (!options || !options.targetLang) return ''
  const { targetLang, style, ignoreMath, ignoreTable, ignoreRefs } = options
  return `?target_lang=${encodeURIComponent(targetLang)}&style=${style}&ignore_math=${ignoreMath}&ignore_table=${ignoreTable}&ignore_refs=${ignoreRefs}`
}

export async function fetchLibrary(options = {}) {
  const res = await fetch(`${API_BASE}/library${buildQuery(options)}`)
  if (!res.ok) throw new Error('라이브러리 조회 실패')
  return res.json()
}

export async function fetchLibraryDoc(docId, options = {}) {
  const res = await fetch(`${API_BASE}/library/${docId}${buildQuery(options)}`)
  if (!res.ok) throw new Error('문서 조회 실패')
  return res.json()
}

export async function fetchLibraryTranslation(docId, pageNum, options = {}) {
  const res = await fetch(`${API_BASE}/library/${docId}/translation/${pageNum}${buildQuery(options)}`)
  if (!res.ok) throw new Error('번역 조회 실패')
  return res.json()
}

export async function deleteLibraryDoc(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('삭제 실패')
  return res.json()
}

export async function fetchLibraryDocImages(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}/images`)
  if (!res.ok) throw new Error('이미지 정보 조회 실패')
  return res.json()
}

export async function updateLibraryDocMetadata(docId, payload) {
  const res = await fetch(`${API_BASE}/library/${docId}/metadata`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error('메타데이터 업데이트 실패')
  return res.json()
}

export async function updateLibraryTranslation(docId, pageNum, payload, options = {}) {
  const res = await fetch(`${API_BASE}/library/${docId}/translation/${pageNum}${buildQuery(options)}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error('번역 수정 저장 실패')
  return res.json()
}
export async function exportAnnotatedPdf(docId, payload) {
  const res = await fetch(`${API_BASE}/library/${docId}/export-pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  if (!res.ok) {
    let message = 'PDF 내보내기 실패'
    try {
      const err = await res.json()
      message = err.detail || message
    } catch {
      // JSON이 아닌 경우 기본 메시지 사용
    }
    throw new Error(message)
  }
  return res.blob()
}

export async function searchLibrary(query) {
  const res = await fetch(`${API_BASE}/library/search?q=${encodeURIComponent(query)}`)
  if (!res.ok) throw new Error('검색 실패')
  return res.json()
}

async function readApiError(res, fallback) {
  try {
    const data = await res.json()
    return data.detail || fallback
  } catch {
    return fallback
  }
}

export async function fetchLibraryFolders() {
  const res = await fetch(`${API_BASE}/library/folders`)
  if (!res.ok) throw new Error(await readApiError(res, '폴더 조회 실패'))
  return res.json()
}

export async function createLibraryFolder(name) {
  const res = await fetch(`${API_BASE}/library/folders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error(await readApiError(res, '폴더 생성 실패'))
  return res.json()
}

export async function renameLibraryFolder(folderId, name) {
  const res = await fetch(`${API_BASE}/library/folders/${folderId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error(await readApiError(res, '폴더 이름 변경 실패'))
  return res.json()
}

export async function deleteLibraryFolder(folderId) {
  const res = await fetch(`${API_BASE}/library/folders/${folderId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await readApiError(res, '폴더 삭제 실패'))
  return res.json()
}

export async function moveLibraryDocToFolder(docId, folderId) {
  const res = await fetch(`${API_BASE}/library/${docId}/folder`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder_id: folderId }),
  })
  if (!res.ok) throw new Error(await readApiError(res, '논문 이동 실패'))
  return res.json()
}

export async function fetchLibraryReferences(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}/references`)
  if (!res.ok) throw new Error('참고문헌 목록 조회 실패')
  return res.json()
}

export async function resolveLibraryReference(docId, refNum, { refresh = false } = {}) {
  const query = refresh ? '?refresh=true' : ''
  const res = await fetch(`${API_BASE}/library/${docId}/references/${encodeURIComponent(refNum)}${query}`)
  if (!res.ok) {
    if (res.status === 404) return null
    throw new Error('참고문헌 링크 조회 실패')
  }
  return res.json()
}

export async function fetchLibraryReferenceInsight(docId, refNum, surroundingContext) {
  const res = await fetch(`${API_BASE}/library/${docId}/references/${encodeURIComponent(refNum)}/insight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ surrounding_context: surroundingContext || '' }),
  })
  if (!res.ok) {
    try {
      const error = await res.json()
      throw new Error(error.detail || '인용 관계 분석 실패')
    } catch (error) {
      if (error instanceof Error && error.message !== '인용 관계 분석 실패') throw error
      throw new Error('인용 관계 분석 실패')
    }
  }
  return res.json()
}

export async function downloadLibraryReference(docId, refNum) {
  const res = await fetch(`${API_BASE}/library/${docId}/references/${encodeURIComponent(refNum)}/download`)
  if (!res.ok) {
    try {
      const error = await res.json()
      throw new Error(error.detail || '인용 논문 다운로드 실패')
    } catch (error) {
      if (error instanceof Error && error.message !== '인용 논문 다운로드 실패') throw error
      throw new Error('인용 논문 다운로드 실패')
    }
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const utf8Match = disposition.match(/filename\\*=UTF-8''([^;]+)/i)
  const plainMatch = disposition.match(/filename=\"?([^\";]+)\"?/i)
  let filename = 'reference.pdf'
  try {
    filename = decodeURIComponent(utf8Match?.[1] || plainMatch?.[1] || filename)
  } catch {}
  return { blob: await res.blob(), filename }
}

export async function fetchLibraryTrash(options = {}) {
  const res = await fetch(`${API_BASE}/library/trash${buildQuery(options)}`)
  if (!res.ok) throw new Error('휴지통 조회 실패')
  return res.json()
}

export async function restoreLibraryDoc(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}/restore`, { method: 'POST' })
  if (!res.ok) throw new Error('복원 실패')
  return res.json()
}

export async function emptyLibraryTrash() {
  const res = await fetch(`${API_BASE}/library/trash/empty`, { method: 'DELETE' })
  if (!res.ok) throw new Error('휴지통 비우기 실패')
  return res.json()
}

export async function deleteLibraryDocPermanently(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}/permanent`, { method: 'DELETE' })
  if (!res.ok) throw new Error('영구 삭제 실패')
  return res.json()
}

// 캐시가 없는 문서는 백엔드가 생성을 백그라운드로 돌리고 매 요청마다
// {status: 'pending'}만 즉시 반환한다(계보/실험 흐름/용어집까지 만드는 지금의
// 프롬프트는 로컬 LLM 기준 수 분씩 걸릴 수 있어, 하나의 요청을 그만큼 열어두면
// 리버스 프록시의 기본 read timeout에 걸려 끊기기 때문). 완료될 때까지 짧은
// 간격으로 재조회한다.
const PRIMER_POLL_INTERVAL_MS = 3000
const PRIMER_POLL_MAX_ATTEMPTS = 150 // 최대 약 7.5분 대기

export async function fetchPrimer(docId, targetLang = '한국어') {
  for (let attempt = 0; attempt < PRIMER_POLL_MAX_ATTEMPTS; attempt++) {
    const res = await fetch(`${API_BASE}/library/${docId}/primer?target_lang=${encodeURIComponent(targetLang)}`)
    if (!res.ok) throw new Error('읽기 전 브리핑 조회 실패')
    const data = await res.json()
    if (data.status !== 'pending') return data
    await new Promise(resolve => setTimeout(resolve, PRIMER_POLL_INTERVAL_MS))
  }
  throw new Error('읽기 전 브리핑 생성이 너무 오래 걸립니다.')
}

// 캐시된 브리핑을 지우고 처음부터 다시 생성을 시작시킨다("다시 생성하기").
// 이 호출 자체는 즉시 pending ack만 받고, 실제 완료 대기는 뒤이어 호출하는
// fetchPrimer()의 폴링 루프가 맡는다.
export async function regeneratePrimer(docId, targetLang = '한국어') {
  const res = await fetch(
    `${API_BASE}/library/${docId}/primer/regenerate?target_lang=${encodeURIComponent(targetLang)}`,
    { method: 'POST' }
  )
  if (!res.ok) throw new Error('브리핑 재생성 요청 실패')
  return res.json()
}

export async function fetchPaperNotes() {
  const res = await fetch(`${API_BASE}/notes`, { cache: 'no-store' })
  if (!res.ok) throw new Error('논문 노트 목록을 불러오지 못했습니다.')
  return res.json()
}

export async function fetchPaperNote(docId) {
  const res = await fetch(`${API_BASE}/notes/${encodeURIComponent(docId)}`, { cache: 'no-store' })
  if (!res.ok) throw new Error('논문 노트를 불러오지 못했습니다.')
  return res.json()
}

export async function regeneratePaperNote(docId, language = '한국어') {
  const res = await fetch(
    `${API_BASE}/notes/${encodeURIComponent(docId)}/regenerate?language=${encodeURIComponent(language)}`,
    { method: 'POST' }
  )
  if (!res.ok) throw new Error('논문 노트 생성을 시작하지 못했습니다.')
  return res.json()
}
