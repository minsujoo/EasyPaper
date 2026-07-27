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

export async function fetchLibraryReferences(docId) {
  const res = await fetch(`${API_BASE}/library/${docId}/references`)
  if (!res.ok) throw new Error('참고문헌 목록 조회 실패')
  return res.json()
}

export async function resolveLibraryReference(docId, refNum) {
  const res = await fetch(`${API_BASE}/library/${docId}/references/${encodeURIComponent(refNum)}`)
  if (!res.ok) {
    if (res.status === 404) return null
    throw new Error('참고문헌 링크 조회 실패')
  }
  return res.json()
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

export async function fetchPrimer(docId, targetLang = '한국어') {
  const res = await fetch(`${API_BASE}/library/${docId}/primer?target_lang=${encodeURIComponent(targetLang)}`)
  if (!res.ok) throw new Error('읽기 전 브리핑 조회 실패')
  return res.json()
}
