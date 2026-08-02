const {
  FuzzySuggestModal,
  ItemView,
  Modal,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  TFile,
  MarkdownRenderer,
  addIcon,
  normalizePath,
  requestUrl,
} = require('obsidian');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');
const { pathToFileURL } = require('url');

const VIEW_TYPE = 'paper-research-workspace-view';
const PAPER_RESEARCH_ICON = 'paper-research-sparkles';
const PAPER_RESEARCH_ICON_SVG = `
  <g transform="scale(4.1666667)" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">
    <path d="M13.5 2.75H6.75a2 2 0 0 0-2 2v14.5a2 2 0 0 0 2 2h7.5" />
    <path d="M13.5 2.75v5h5" />
    <path d="M8 9.75h4.25M8 13h3" />
    <path d="M17.25 10.5c.35 2.1 1.4 3.15 3.5 3.5-2.1.35-3.15 1.4-3.5 3.5-.35-2.1-1.4-3.15-3.5-3.5 2.1-.35 3.15-1.4 3.5-3.5Z" />
    <path d="M18.5 7.5h.01" />
  </g>
`;
const GENERATED_START = '<!-- paper-research-workspace:start -->';
const GENERATED_END = '<!-- paper-research-workspace:end -->';

const DEFAULT_SETTINGS = {
  connectionFile: '',
  backendExecutable: defaultBackendExecutable(),
  dataDirectory: '',
  noteFolder: 'Research/Papers',
  pdfFolder: 'Research/Papers/PDF',
  assetFolder: 'Research/Papers/assets',
  imports: {},
  notePaths: {},
  pdfPaths: {},
  pdfHighlights: {},
};

const AI_PROVIDER_CONFIG = [
  {
    id: 'codex', label: 'Codex', effort: true,
    models: [
      ['gpt-5.6-terra', 'GPT-5.6 Terra'],
      ['gpt-5.6-luna', 'GPT-5.6 Luna'],
      ['gpt-5.5', 'GPT-5.5'],
    ],
  },
  {
    id: 'claude_code', label: 'Claude Code', effort: true,
    models: [['sonnet', 'Sonnet 5'], ['fable', 'Fable 5'], ['opus', 'Opus 4.8'], ['haiku', 'Haiku 4.5']],
  },
  {
    id: 'antigravity', label: 'Antigravity', effort: false,
    models: [
      ['Gemini 3.6 Flash (Low)', 'Gemini 3.6 Flash · Low'],
      ['Gemini 3.6 Flash (Medium)', 'Gemini 3.6 Flash · Medium'],
      ['Gemini 3.6 Flash (High)', 'Gemini 3.6 Flash · High'],
      ['Gemini 3.1 Pro (Low)', 'Gemini 3.1 Pro · Low'],
      ['Gemini 3.1 Pro (High)', 'Gemini 3.1 Pro · High'],
      ['Claude Sonnet 4.6 (Thinking)', 'Claude Sonnet 4.6 · Thinking'],
      ['Claude Opus 4.6 (Thinking)', 'Claude Opus 4.6 · Thinking'],
    ],
  },
  { id: 'ollama', label: 'Ollama (로컬)', effort: false, models: [] },
  {
    id: 'openai', label: 'OpenAI API', effort: false,
    models: [['gpt-5.5-pro', 'GPT-5.5 Pro'], ['gpt-5.5', 'GPT-5.5'], ['gpt-5.4', 'GPT-5.4'], ['gpt-5.4-mini', 'GPT-5.4 Mini'], ['o3', 'o3'], ['gpt-4o', 'GPT-4o']],
  },
  {
    id: 'claude', label: 'Anthropic API', effort: false,
    models: [['claude-opus-4.8', 'Claude Opus 4.8'], ['claude-sonnet-4.6', 'Claude Sonnet 4.6'], ['claude-haiku-4.5', 'Claude Haiku 4.5']],
  },
  {
    id: 'gemini', label: 'Google Gemini API', effort: false,
    models: [['gemini-3.5-flash', 'Gemini 3.5 Flash'], ['gemini-3.1-pro', 'Gemini 3.1 Pro']],
  },
];

const AI_EFFORTS = [['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['xhigh', 'xHigh'], ['max', 'Max']];

function aiProviderConfig(provider) {
  return AI_PROVIDER_CONFIG.find((item) => item.id === provider) || AI_PROVIDER_CONFIG[0];
}

function splitAIModel(provider, storedModel) {
  const config = aiProviderConfig(provider);
  const raw = String(storedModel || '').trim();
  if (config.effort && raw.includes('|')) {
    const [model, effort] = raw.split('|', 2);
    return { model, effort: effort || 'medium' };
  }
  return { model: raw, effort: config.effort ? 'medium' : '' };
}

function joinAIModel(provider, model, effort) {
  return aiProviderConfig(provider).effort ? `${model}|${effort || 'medium'}` : model;
}

let pdfJsCache = null;
let pdfJsBaseDir = '';
let pdfJsWorkerSrc = '';
function getPdfJs() {
  if (pdfJsCache) return pdfJsCache;
  const baseDir = pdfJsBaseDir || __dirname;
  const pdfJsPath = path.join(baseDir, 'pdfjs', 'pdf.js');
  const workerPath = path.join(baseDir, 'pdfjs', 'pdf.worker.js');
  if (!fs.existsSync(pdfJsPath) || !fs.existsSync(workerPath)) {
    throw new Error('PDF 리더 구성요소가 설치되지 않았습니다.');
  }
  pdfJsCache = require(pdfJsPath);
  pdfJsCache.GlobalWorkerOptions.workerSrc = pdfJsWorkerSrc || pathToFileURL(workerPath).href;
  return pdfJsCache;
}

function normalizedPaperText(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

function stripMarkdown(value) {
  return String(value || '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`~#>]/g, '')
    .trim();
}

function translationMarkdown(value) {
  const math = [];
  let text = String(value || '').replace(/\f/g, '').trim();
  text = text.replace(/(\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?:\\.|[^$\n])+\$)/g, (segment) => {
    const normalized = segment.startsWith('\\[') ? `$$${segment.slice(2, -2)}$$`
      : segment.startsWith('\\(') ? `$${segment.slice(2, -2)}$` : segment;
    const token = `MATHSEGMENT${math.length}TOKEN`;
    math.push(normalized); return token;
  });
  text = stripMarkdown(text);
  // Use a replacer callback so Markdown math delimiters such as `$$` are
  // restored verbatim. Passing the segment as a replacement string makes
  // String.replace interpret `$$` as a single literal dollar sign.
  math.forEach((segment, index) => {
    text = text.replace(`MATHSEGMENT${index}TOKEN`, () => segment);
  });
  return text;
}

function parseReferenceNumbers(value) {
  const found = new Set();
  for (const match of String(value || '').matchAll(/\[\s*(\d+(?:\s*[-,]\s*\d+)*)\s*\]/g)) {
    const parts = match[1].split(',');
    for (const part of parts) {
      const range = part.trim().match(/^(\d+)\s*-\s*(\d+)$/);
      if (range) {
        const start = Number(range[1]); const end = Number(range[2]);
        if (end >= start && end - start <= 30) for (let n = start; n <= end; n += 1) found.add(String(n));
      } else if (/^\d+$/.test(part.trim())) found.add(String(Number(part.trim())));
    }
  }
  return [...found];
}

function defaultConnectionFile() {
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'paper-research-workspace', 'integration.json');
  }
  if (process.platform === 'win32') {
    return path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'com.easypaper.desktop', 'integration.json');
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), 'com.easypaper.desktop', 'integration.json');
}

function defaultBackendExecutable() {
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'paper-research-workspace', 'engine', 'easypaper-backend');
  }
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'EasyPaper', 'binaries', 'easypaper-backend', 'easypaper-backend.exe');
  }
  return '/usr/lib/EasyPaper/binaries/easypaper-backend/easypaper-backend';
}

function shouldMigrateBackendExecutable(value) {
  const configured = String(value || '').trim();
  if (!configured) return true;
  if (process.platform === 'darwin') return configured.startsWith('/usr/lib/') || configured.endsWith('.exe');
  if (process.platform === 'win32') return configured.startsWith('/') || !configured.toLowerCase().endsWith('.exe');
  return false;
}

function safeName(value, fallback = 'paper') {
  const cleaned = String(value || '')
    .replace(/[\\/:*?"<>|#^[\]]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return (cleaned || fallback).slice(0, 140);
}

function yamlString(value) {
  return JSON.stringify(String(value ?? ''));
}

function asList(value) {
  if (Array.isArray(value)) return value.filter(Boolean).map(String);
  if (!value) return [];
  return String(value).split(/\s*(?:,|;|\band\b)\s*/i).filter(Boolean);
}

function markdownList(items) {
  const values = Array.isArray(items) ? items : [];
  return values.length ? values.map((item) => `- ${typeof item === 'string' ? item : JSON.stringify(item)}`).join('\n') : '-';
}

class PaperPicker extends FuzzySuggestModal {
  constructor(app, papers, onChoose) {
    super(app);
    this.papers = papers;
    this.onChoose = onChoose;
    this.setPlaceholder('내보낼 논문을 선택하세요');
  }
  getItems() { return this.papers; }
  getItemText(item) {
    const meta = item.metadata || {};
    return meta.title || item.title || item.filename || item.doc_id || item.id;
  }
  onChooseItem(item) { void this.onChoose(item); }
}

class VocabularyModal extends Modal {
  constructor(app, seed, onSave) {
    super(app);
    this.seed = seed;
    this.onSave = onSave;
  }
  onOpen() {
    const { contentEl } = this;
    contentEl.addClass('paper-vocab-modal');
    contentEl.createEl('h2', { text: '논문 단어 카드' });
    const term = contentEl.createEl('input', { type: 'text', value: this.seed.term });
    const meaning = contentEl.createEl('input', { type: 'text', value: this.seed.meaning_ko || '', placeholder: '문맥상 뜻' });
    const contextEn = contentEl.createEl('textarea');
    contextEn.value = this.seed.context_en || '';
    const contextKo = contentEl.createEl('textarea');
    contextKo.value = this.seed.context_ko || '';
    const actions = contentEl.createDiv('paper-vocab-actions');
    actions.createEl('button', { text: '취소' }).onclick = () => this.close();
    const save = actions.createEl('button', { text: '저장하고 Anki로 보내기', cls: 'mod-cta' });
    save.onclick = async () => {
      if (!term.value.trim() || !meaning.value.trim()) {
        new Notice('단어와 뜻을 입력하세요.');
        return;
      }
      save.disabled = true;
      try {
        await this.onSave({
          ...this.seed,
          term: term.value.trim(),
          meaning_ko: meaning.value.trim(),
          context_en: contextEn.value.trim(),
          context_ko: contextKo.value.trim(),
        });
        this.close();
      } finally {
        save.disabled = false;
      }
    };
  }
  onClose() { this.contentEl.empty(); }
}

function paperTitle(item) {
  const meta = item?.metadata || {};
  return meta.title || item?.title || item?.filename || item?.doc_id || item?.id || '제목 없음';
}

function plainAuthors(value) {
  return asList(value).slice(0, 5).join(', ');
}

function scholarBibtex(paper) {
  const authorName = asList(paper?.authors)[0] || 'unknown';
  const firstAuthor = authorName.split(/\s+/).pop().replace(/[^A-Za-z0-9]/g, '') || 'paper';
  const key = `${firstAuthor}${paper?.year || ''}`;
  const fields = [
    `  title = {${paper?.title || ''}}`,
    asList(paper?.authors).length ? `  author = {${asList(paper.authors).join(' and ')}}` : '',
    paper?.year ? `  year = {${paper.year}}` : '',
    paper?.venue ? `  booktitle = {${paper.venue}}` : '',
    paper?.doi ? `  doi = {${String(paper.doi).replace(/^https:\/\/doi\.org\//i, '')}}` : '',
    paper?.url ? `  url = {${paper.url}}` : '',
  ].filter(Boolean);
  return `@article{${key},\n${fields.join(',\n')}\n}`;
}

class ReviewModal extends Modal {
  constructor(app, plugin) { super(app); this.plugin = plugin; this.card = null; this.revealed = false; }
  async onOpen() {
    this.contentEl.addClass('paper-review-modal');
    this.scope.register([], 'Space', () => void this.reveal());
    [1, 2, 3, 4].forEach((ease) => this.scope.register([], String(ease), () => void this.answer(ease)));
    await this.start();
  }
  async start() {
    this.contentEl.empty();
    this.contentEl.createEl('h2', { text: '단어 복습' });
    this.contentEl.createDiv('paper-loading').setText('Anki 복습을 준비하는 중…');
    try {
      const result = (await this.plugin.api('/api/vocabulary/review/start', { method: 'POST', json: {} })).json;
      this.card = result.card;
      this.revealed = false;
      this.render();
    } catch (error) { this.renderError(error); }
  }
  renderError(error) {
    this.contentEl.empty();
    this.contentEl.createEl('h2', { text: '복습을 시작하지 못했습니다' });
    this.contentEl.createEl('p', { text: error.message });
  }
  render() {
    this.contentEl.empty();
    this.contentEl.createEl('h2', { text: '단어 복습' });
    if (!this.card) {
      this.contentEl.createDiv('paper-empty').setText('오늘 복습할 카드가 없습니다.');
      return;
    }
    const front = this.contentEl.createDiv('paper-review-front');
    front.innerHTML = this.card.front || '';
    if (this.revealed) {
      const back = this.contentEl.createDiv('paper-review-back');
      back.innerHTML = this.card.back || '';
      const actions = this.contentEl.createDiv('paper-review-actions');
      const labels = ['다시', '어려움', '좋음', '쉬움'];
      labels.forEach((label, index) => {
        const interval = this.card.next_reviews?.[index] ? ` · ${this.card.next_reviews[index]}` : '';
        actions.createEl('button', { text: `${index + 1} ${label}${interval}` }).onclick = () => void this.answer(index + 1);
      });
    } else {
      this.contentEl.createEl('button', { text: '답 보기 · Space', cls: 'mod-cta paper-review-reveal' }).onclick = () => void this.reveal();
    }
  }
  async reveal() {
    if (!this.card || this.revealed) return;
    try {
      const result = (await this.plugin.api('/api/vocabulary/review/reveal', { method: 'POST', json: {} })).json;
      this.card = result.card || this.card;
      this.revealed = true;
      this.render();
    } catch (error) { new Notice(error.message); }
  }
  async answer(ease) {
    if (!this.card || !this.revealed) return;
    try {
      const result = (await this.plugin.api('/api/vocabulary/review/answer', { method: 'POST', json: { ease } })).json;
      this.card = result.card;
      this.revealed = false;
      this.render();
    } catch (error) { new Notice(error.message); }
  }
  onClose() { this.contentEl.empty(); }
}

class AISettingsModal extends Modal {
  constructor(app, plugin, view) {
    super(app);
    this.plugin = plugin;
    this.view = view;
    this.system = null;
    this.availability = {};
  }

  async onOpen() {
    this.modalEl.addClass('paper-ai-settings-shell');
    this.contentEl.addClass('paper-ai-settings-modal');
    this.contentEl.createEl('h2', { text: 'AI 모델 설정' });
    this.contentEl.createDiv('paper-loading').setText('현재 설정과 설치 상태를 확인하는 중…');
    try {
      const [systemResponse, availabilityResponse] = await Promise.all([
        this.plugin.api('/api/settings/system'),
        this.plugin.api('/api/availability'),
      ]);
      this.system = systemResponse.json;
      this.availability = availabilityResponse.json || {};
      this.render();
    } catch (error) {
      this.contentEl.empty();
      this.contentEl.createEl('h2', { text: 'AI 모델 설정' });
      this.contentEl.createDiv('paper-error').setText(error.message);
    }
  }

  providerAvailable(provider) {
    if (provider === 'codex' || provider === 'claude_code' || provider === 'antigravity') return this.availability[provider] === true;
    if (provider === 'ollama') return (this.system.available_models || []).length > 0;
    if (provider === 'openai') return !!this.system.openai_api_key;
    if (provider === 'claude') return !!this.system.claude_api_key;
    if (provider === 'gemini') return !!this.system.gemini_api_key;
    return false;
  }

  modelsFor(provider, currentModel = '') {
    let models = aiProviderConfig(provider).models.slice();
    if (provider === 'ollama') models = (this.system.available_models || []).map((model) => [model, model]);
    if (currentModel && !models.some(([value]) => value === currentModel)) models.unshift([currentModel, currentModel]);
    return models;
  }

  addSelectField(parent, label, className) {
    const row = parent.createDiv('paper-ai-setting-row');
    row.createEl('label', { text: label });
    return row.createEl('select', { cls: className });
  }

  fillProviderSelect(select, current) {
    select.empty();
    AI_PROVIDER_CONFIG.forEach((provider) => {
      const available = this.providerAvailable(provider.id);
      const option = select.createEl('option', {
        value: provider.id,
        text: `${provider.label}${available ? '' : ' · 사용 불가'}`,
      });
      option.disabled = !available && provider.id !== current;
    });
    select.value = current;
  }

  refreshModelFields(providerSelect, modelSelect, effortSelect, storedModel = '') {
    const provider = providerSelect.value;
    const parsed = splitAIModel(provider, storedModel);
    const models = this.modelsFor(provider, parsed.model);
    modelSelect.empty();
    models.forEach(([value, label]) => modelSelect.createEl('option', { value, text: label }));
    modelSelect.value = models.some(([value]) => value === parsed.model) ? parsed.model : (models[0]?.[0] || '');
    const supportsEffort = aiProviderConfig(provider).effort;
    effortSelect.parentElement.toggleClass('is-hidden', !supportsEffort);
    effortSelect.empty();
    AI_EFFORTS.forEach(([value, label]) => effortSelect.createEl('option', { value, text: label }));
    effortSelect.value = AI_EFFORTS.some(([value]) => value === parsed.effort) ? parsed.effort : 'medium';
  }

  render() {
    const content = this.contentEl;
    content.empty();
    content.createEl('h2', { text: 'AI 모델 설정' });
    content.createEl('p', { cls: 'paper-ai-settings-help', text: '번역과 설명·채팅에 사용할 모델을 각각 선택할 수 있습니다. 사용 불가 항목은 CLI 설치 또는 API 키가 필요합니다.' });

    const transGroup = content.createDiv('paper-ai-settings-group');
    transGroup.createEl('h3', { text: '번역' });
    const transProvider = this.addSelectField(transGroup, '제공자', 'paper-ai-trans-provider');
    const transModel = this.addSelectField(transGroup, '모델', 'paper-ai-trans-model');
    const transEffort = this.addSelectField(transGroup, '추론 강도', 'paper-ai-trans-effort');
    this.fillProviderSelect(transProvider, this.system.trans_provider || 'codex');
    this.refreshModelFields(transProvider, transModel, transEffort, this.system.trans_model || '');

    const sameRow = content.createEl('label', { cls: 'paper-ai-same-model' });
    const same = sameRow.createEl('input', { type: 'checkbox' });
    same.checked = this.system.trans_provider === this.system.chat_provider && this.system.trans_model === this.system.chat_model;
    sameRow.createSpan({ text: '설명·채팅에도 같은 모델 사용' });

    const chatGroup = content.createDiv('paper-ai-settings-group');
    chatGroup.createEl('h3', { text: '설명·채팅' });
    const chatProvider = this.addSelectField(chatGroup, '제공자', 'paper-ai-chat-provider');
    const chatModel = this.addSelectField(chatGroup, '모델', 'paper-ai-chat-model');
    const chatEffort = this.addSelectField(chatGroup, '추론 강도', 'paper-ai-chat-effort');
    this.fillProviderSelect(chatProvider, this.system.chat_provider || 'codex');
    this.refreshModelFields(chatProvider, chatModel, chatEffort, this.system.chat_model || '');

    const syncChatVisibility = () => chatGroup.toggleClass('is-hidden', same.checked);
    syncChatVisibility();
    same.onchange = syncChatVisibility;
    transProvider.onchange = () => this.refreshModelFields(transProvider, transModel, transEffort);
    chatProvider.onchange = () => this.refreshModelFields(chatProvider, chatModel, chatEffort);

    const scholarGroup = content.createDiv('paper-ai-settings-group');
    scholarGroup.createEl('h3', { text: '학술 검색' });
    const scholarKeyRow = scholarGroup.createDiv('paper-ai-setting-row');
    scholarKeyRow.createEl('label', { text: 'Semantic Scholar API 키' });
    const scholarKeyControl = scholarKeyRow.createDiv('paper-secret-input');
    const scholarKey = scholarKeyControl.createEl('input', {
      type: 'password',
      value: '',
      placeholder: this.system.semantic_scholar_api_key_set ? '저장된 키 사용 중' : 's2k-…',
      attr: { autocomplete: 'off', spellcheck: 'false' },
    });
    scholarKey.dataset.remove = 'false';
    const revealScholarKey = scholarKeyControl.createEl('button', { text: '보기', attr: { type: 'button' } });
    revealScholarKey.onclick = () => {
      const reveal = scholarKey.type === 'password';
      scholarKey.type = reveal ? 'text' : 'password';
      revealScholarKey.setText(reveal ? '숨기기' : '보기');
    };
    const removeScholarKey = scholarKeyControl.createEl('button', { text: '삭제', attr: { type: 'button' } });
    removeScholarKey.onclick = () => {
      scholarKey.value = '';
      scholarKey.dataset.remove = 'true';
      scholarKey.placeholder = '저장하면 키가 삭제됩니다';
    };
    scholarKey.oninput = () => {
      if (scholarKey.value) scholarKey.dataset.remove = 'false';
    };
    scholarGroup.createEl('p', {
      cls: 'paper-ai-settings-help',
      text: '검색 요청은 OpenAlex와 결합되며 Semantic Scholar 제한에 맞춰 1.1초 이상 간격으로 전송합니다.',
    });

    const integrationGroup = content.createDiv('paper-ai-settings-group');
    integrationGroup.createEl('h3', { text: '단어장 연동' });
    const ankiRow = integrationGroup.createEl('label', { cls: 'paper-ai-same-model' });
    const ankiAutoLaunch = ankiRow.createEl('input', { type: 'checkbox' });
    ankiAutoLaunch.checked = this.system.anki_auto_launch === true;
    ankiRow.createSpan({ text: '연구 공간을 시작할 때 Anki도 실행' });
    integrationGroup.createEl('p', {
      cls: 'paper-ai-settings-help',
      text: '꺼져 있어도 내장 단어장과 복습은 그대로 작동합니다. AnkiConnect 외부 덱 동기화를 사용할 때만 켜세요.',
    });

    const status = content.createDiv('paper-ai-settings-status');
    const actions = content.createDiv('paper-ai-settings-actions');
    actions.createEl('button', { text: '취소' }).onclick = () => this.close();
    const save = actions.createEl('button', { text: '저장', cls: 'mod-cta' });
    save.onclick = async () => {
      if (!transModel.value || (!same.checked && !chatModel.value)) {
        status.setText('사용할 모델을 선택하세요.');
        return;
      }
      save.disabled = true;
      status.setText('설정을 저장하는 중…');
      const transStored = joinAIModel(transProvider.value, transModel.value, transEffort.value);
      const chatStored = same.checked ? transStored : joinAIModel(chatProvider.value, chatModel.value, chatEffort.value);
      const chatProviderValue = same.checked ? transProvider.value : chatProvider.value;
      try {
        await this.plugin.api('/api/settings/system', { method: 'POST', json: {
          ollama_host: this.system.ollama_host || 'http://localhost:11434',
          trans_provider: transProvider.value,
          trans_model: transStored,
          chat_provider: chatProviderValue,
          chat_model: chatStored,
          openai_api_key: this.system.openai_api_key || '',
          gemini_api_key: this.system.gemini_api_key || '',
          claude_api_key: this.system.claude_api_key || '',
          openalex_mailto: this.system.openalex_mailto || '',
          semantic_scholar_api_key: scholarKey.value.trim() || (scholarKey.dataset.remove === 'true' ? '' : null),
          translation_prompt_template: this.system.translation_prompt_template || '',
          anki_auto_launch: ankiAutoLaunch.checked,
        }});
        status.setText('저장했습니다. 새 요청부터 선택한 모델을 사용합니다.');
        new Notice('AI 모델 설정을 저장했습니다.');
        await this.view?.refreshAIButtonLabel();
        window.setTimeout(() => this.close(), 500);
      } catch (error) {
        status.setText(error.message);
        save.disabled = false;
      }
    };
  }

  onClose() {
    this.modalEl.removeClass('paper-ai-settings-shell');
    this.contentEl.empty();
  }
}

class ResearchExplorer {
  constructor(app, plugin) {
    this.app = app;
    this.plugin = plugin;
  }

  render(parent) {
    const content = parent.createDiv('paper-scholar-tools paper-research-explorer');
    const header = content.createDiv('paper-page-header');
    const title = header.createDiv();
    title.createEl('h2', { text: '연구 탐색' });
    title.createEl('p', { text: '논문의 연결 관계와 학회 일정을 한곳에서 살펴봅니다.' });
    const tabs = content.createDiv('paper-scholar-tool-tabs');
    const pane = content.createDiv('paper-scholar-tool-pane');
    const buttons = {};
    const show = (name) => {
      Object.entries(buttons).forEach(([key, button]) => button.toggleClass('is-active', key === name));
      if (name === 'map') void this.renderMap(pane);
      else if (name === 'conferences') void this.renderConferences(pane);
      else void this.renderBackground(pane);
    };
    [['map', '관계 지도'], ['conferences', '학회 일정'], ['background', '백그라운드']].forEach(([key, label]) => {
      buttons[key] = tabs.createEl('button', { text: label });
      buttons[key].onclick = () => show(key);
    });
    show('map');
  }

  async renderMap(pane) {
    pane.empty(); pane.createDiv('paper-loading').setText('보관함 논문을 불러오는 중…');
    try {
      const papers = (await this.plugin.api('/api/scholar/tools/papers')).json.papers || [];
      pane.empty();
      if (!papers.length) { pane.createDiv('paper-empty').setText('보관함이나 북마크에 논문을 추가하세요.'); return; }
      const controls = pane.createDiv('paper-scholar-map-controls');
      const select = controls.createEl('select', { attr: { 'aria-label': '관계 지도를 만들 논문' } });
      papers.forEach((paper, index) => select.createEl('option', { value: String(index), text: paper.title || '제목 없음' }));
      const build = controls.createEl('button', { text: '지도 만들기', cls: 'mod-cta' });
      const canvas = pane.createDiv('paper-scholar-map-canvas');
      build.onclick = async () => {
        canvas.empty(); canvas.createDiv('paper-loading').setText('인용·참고문헌·유사 논문을 연결하는 중…');
        build.disabled = true;
        try {
          const graph = (await this.plugin.api('/api/scholar/map', {
            method: 'POST', json: { paper: papers[Number(select.value)] },
          })).json;
          this.drawGraph(canvas, graph);
        } catch (error) { canvas.empty(); canvas.createDiv('paper-error').setText(error.message); }
        finally { build.disabled = false; }
      };
    } catch (error) { pane.empty(); pane.createDiv('paper-error').setText(error.message); }
  }

  drawGraph(parent, graph) {
    parent.empty();
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 1000 620');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', '논문 관계 지도');
    const nodes = graph.nodes || [];
    const rootId = String(graph.root_id || '');
    const groups = { reference: [], citation: [], similar: [] };
    nodes.forEach((node) => { if (groups[node.group]) groups[node.group].push(node); });
    const positions = new Map([[rootId, { x: 500, y: 285 }]]);
    const place = (items, cx, cy, rx, ry, start, span) => items.forEach((node, index) => {
      const angle = start + span * ((index + .5) / Math.max(1, items.length));
      positions.set(String(node.id), { x: cx + Math.cos(angle) * rx, y: cy + Math.sin(angle) * ry });
    });
    place(groups.reference, 390, 285, 330, 245, Math.PI * .55, Math.PI * .9);
    place(groups.citation, 610, 285, 330, 245, -Math.PI * .45, Math.PI * .9);
    place(groups.similar, 500, 260, 300, 280, Math.PI * .12, Math.PI * .76);
    const edgeLayer = document.createElementNS(ns, 'g');
    (graph.edges || []).forEach((edge) => {
      const source = positions.get(String(edge.source)); const target = positions.get(String(edge.target));
      if (!source || !target) return;
      const line = document.createElementNS(ns, 'line');
      line.setAttribute('x1', source.x); line.setAttribute('y1', source.y);
      line.setAttribute('x2', target.x); line.setAttribute('y2', target.y);
      line.setAttribute('class', `is-${edge.kind || 'similar'}`); edgeLayer.appendChild(line);
    });
    svg.appendChild(edgeLayer);
    nodes.forEach((node) => {
      const position = positions.get(String(node.id)); if (!position) return;
      const group = document.createElementNS(ns, 'g'); group.setAttribute('class', `paper-map-node is-${node.group || 'similar'}`);
      group.setAttribute('transform', `translate(${position.x} ${position.y})`);
      const circle = document.createElementNS(ns, 'circle'); circle.setAttribute('r', node.group === 'root' ? 22 : 12); group.appendChild(circle);
      const label = document.createElementNS(ns, 'text');
      const title = String(node.title || '제목 없음'); label.textContent = title.length > 34 ? `${title.slice(0, 33)}…` : title;
      label.setAttribute('y', node.group === 'root' ? 38 : 28); label.setAttribute('text-anchor', 'middle'); group.appendChild(label);
      const tooltip = document.createElementNS(ns, 'title'); tooltip.textContent = title; group.appendChild(tooltip);
      if (node.url) { group.classList.add('is-clickable'); group.onclick = () => window.open(node.url, '_blank', 'noopener'); }
      svg.appendChild(group);
    });
    parent.appendChild(svg);
    const legend = parent.createDiv('paper-scholar-map-legend');
    [['root', '선택 논문'], ['reference', '참고문헌'], ['citation', '인용한 논문'], ['similar', '유사 논문']]
      .forEach(([kind, label]) => legend.createSpan({ cls: `is-${kind}`, text: label }));
  }

  async renderConferences(pane) {
    pane.empty(); pane.createDiv('paper-loading').setText('학회 일정을 확인하는 중…');
    try {
      const data = (await this.plugin.api('/api/scholar/conferences')).json;
      pane.empty();
      const note = pane.createDiv('paper-scholar-tool-note');
      note.createSpan({ text: data.warning || '일정은 정확하지 않을 수 있으므로 제출 전 공식 홈페이지에서 다시 확인하세요.' });
      if (data.source_url) note.createEl('a', { text: ' 원본 시트', href: data.source_url, attr: { target: '_blank', rel: 'noopener' } });
      if (data.official_updated_at) note.createSpan({ text: ` · 공식 사이트 확인 ${new Date(data.official_updated_at).toLocaleString()}` });
      const conferences = data.conferences || [];
      const priorityLabels = { recommended: '추천', supported: '지원 대상', discuss: '사전 논의', special: '특별한 경우' };
      const counts = conferences.reduce((result, conference) => {
        const submission = conference.submission_status || 'closed';
        result[submission] = (result[submission] || 0) + 1; return result;
      }, {});
      let activeSubmission = counts.open ? 'open' : 'closed';
      let priorityFilter = 'core';
      let watchedOnly = false;
      const controls = pane.createDiv('paper-conference-controls');
      const statusBar = controls.createDiv('paper-conference-status-tabs');
      const submissionButtons = {};
      [['open', '제출 가능'], ['closed', '제출 불가능']].forEach(([status, label]) => {
        const button = statusBar.createEl('button', { text: `${label} ${counts[status] || 0}` });
        submissionButtons[status] = button;
      });
      const filters = controls.createDiv('paper-conference-filters');
      const priority = filters.createEl('select', { attr: { 'aria-label': '학회 적합도' } });
      [['core', '추천·지원'], ['recommended', '추천만'], ['discuss', '사전 논의'], ['special', '특별한 경우'], ['all', '전체 등급']]
        .forEach(([value, label]) => priority.createEl('option', { value, text: label }));
      const watchedLabel = filters.createEl('label', { text: ' 관심만' });
      const watchedInput = watchedLabel.createEl('input', { type: 'checkbox' });
      const refresh = filters.createEl('button', { text: '공식 일정 확인' });
      const list = pane.createDiv('paper-conference-list');
      const cleanDate = (value) => String(value || '').replace(/-\?\?/g, '').replace(/\?/g, '미정');
      const todayText = new Date().toLocaleDateString('sv-SE');
      const isPastDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(String(value || '')) && String(value) < todayText;
      const renderList = () => {
        Object.entries(submissionButtons).forEach(([status, button]) => button.toggleClass('is-active', status === activeSubmission));
        list.empty();
        const visible = conferences
          .filter((conference) => conference.status !== 'past')
          .filter((conference) => (conference.submission_status || 'closed') === activeSubmission)
          .filter((conference) => priorityFilter === 'all'
            || (priorityFilter === 'core' && ['recommended', 'supported'].includes(conference.priority))
            || conference.priority === priorityFilter)
          .filter((conference) => !watchedOnly || conference.watched)
          .sort((left, right) => {
            const leftDeadline = String(left.deadline || '');
            const rightDeadline = String(right.deadline || '');
            const deadlineOrder = activeSubmission === 'open'
              ? leftDeadline.localeCompare(rightDeadline)
              : rightDeadline.localeCompare(leftDeadline);
            return deadlineOrder || String(left.title || '').localeCompare(String(right.title || ''));
          });
        visible.forEach((conference) => {
          const card = list.createDiv(`paper-conference-card is-${conference.priority || 'supported'} is-${conference.status || 'unknown'}`);
          const identity = card.createDiv('paper-conference-identity');
          const heading = identity.createDiv('paper-conference-heading');
          heading.createEl('strong', { text: `${conference.title} ${conference.year || ''}`.trim() });
          heading.createSpan({ cls: `paper-conference-priority is-${conference.priority || 'supported'}`, text: priorityLabels[conference.priority] || '지원 대상' });
          if (conference.schedule_source === 'official') heading.createSpan({ cls: 'paper-conference-official', text: '공식 확인' });
          if (conference.description) identity.createEl('p', { text: conference.description });
          if (conference.about_ko) identity.createEl('p', { cls: 'paper-conference-about', text: conference.about_ko });
          const schedule = card.createDiv('paper-conference-schedule');
          const deadline = conference.deadline ? cleanDate(conference.deadline) : '';
          const deadlineEstimate = conference.deadline_estimated ? ' (추정)' : '';
          const dateEstimate = conference.date_estimated ? ' (추정)' : '';
          const remaining = conference.next_action_kind === 'paper' && Number.isFinite(conference.days_remaining)
            ? (conference.days_remaining === 0 ? 'D-day' : `D-${conference.days_remaining}`) : '';
          if (deadline) schedule.createEl(activeSubmission === 'open' ? 'strong' : 'span', {
            text: `${activeSubmission === 'open' ? '제출' : '제출 마감'} ${deadline}${deadlineEstimate}${remaining ? ` · ${remaining}` : ''}`,
          });
          const registrationRows = [
            ['author_registration_deadline', '저자 등록'],
            ['early_registration_deadline', '조기 등록'],
            ['registration_deadline', '일반 등록'],
          ];
          registrationRows.forEach(([field, label]) => {
            if (!conference[field] || isPastDate(conference[field])) return;
            const estimated = conference[`${field}_estimated`] ? ' (추정)' : '';
            const action = conference.next_action_kind === 'registration'
              && conference.next_action_date === conference[field] && Number.isFinite(conference.days_remaining)
              ? (conference.days_remaining === 0 ? 'D-day' : `D-${conference.days_remaining}`) : '';
            schedule.createEl(action ? 'strong' : 'span', {
              text: `${label} ${cleanDate(conference[field])}${estimated}${action ? ` · ${action}` : ''}`,
            });
          });
          if (conference.registration_open) schedule.createEl('span', {
            cls: 'paper-conference-registration-open', text: '참가 등록 가능',
          });
          const eventAction = conference.next_action_kind === 'event' && Number.isFinite(conference.days_remaining)
            ? (conference.days_remaining === 0 ? 'D-day' : `D-${conference.days_remaining}`) : '';
          schedule.createEl(eventAction ? 'strong' : 'span', {
            text: `개최 ${conference.date ? cleanDate(conference.date) : `${conference.year || ''}년 중 (추정)`}${dateEstimate}${eventAction ? ` · ${eventAction}` : ''}`,
          });
          if (conference.place) schedule.createEl('span', { text: conference.place });
          const actions = card.createDiv('paper-conference-actions');
          const watch = actions.createEl('button', { text: conference.watched ? '★' : '☆', attr: { 'aria-label': '관심 학회' } });
          watch.toggleClass('is-active', Boolean(conference.watched));
          if (conference.official_url) actions.createEl('a', {
            text: Number(conference.official_link_year) === Number(conference.year) ? '공식 사이트' : '학회 홈페이지',
            href: conference.official_url, attr: { target: '_blank', rel: 'noopener' },
          });
          if (conference.registration_url && conference.registration_url !== conference.official_url) actions.createEl('a', {
            text: '등록', href: conference.registration_url, attr: { target: '_blank', rel: 'noopener' },
          });
          watch.onclick = async () => {
            conference.watched = !conference.watched;
            try {
              await this.plugin.api('/api/scholar/conferences/watch', { method: 'POST', json: {
                conference_id: String(conference.id), conference, watched: conference.watched,
              }});
              renderList();
            } catch (error) { conference.watched = !conference.watched; new Notice(error.message); }
          };
        });
        if (!visible.length) list.createDiv('paper-empty').setText('선택한 조건에 맞는 학회가 없습니다.');
      };
      Object.entries(submissionButtons).forEach(([status, button]) => {
        button.onclick = () => { activeSubmission = status; renderList(); };
      });
      priority.onchange = () => { priorityFilter = priority.value; renderList(); };
      watchedInput.onchange = () => { watchedOnly = watchedInput.checked; renderList(); };
      refresh.onclick = async () => {
        refresh.disabled = true; refresh.setText('공식 사이트 확인 중…');
        try {
          const result = (await this.plugin.api('/api/scholar/conferences/refresh', { method: 'POST', json: {} })).json;
          new Notice(`공식 사이트 ${result.sites || 0}곳 확인 · 일정 변경 ${result.changed || 0}건`);
          await this.renderConferences(pane);
        } catch (error) {
          refresh.disabled = false; refresh.setText('공식 일정 확인'); new Notice(error.message);
        }
      };
      renderList();
    } catch (error) { pane.empty(); pane.createDiv('paper-error').setText(error.message); }
  }

  async renderBackground(pane) {
    pane.empty(); pane.createDiv('paper-loading').setText('자동 수집 상태를 확인하는 중…');
    try {
      const status = (await this.plugin.api('/api/scholar/background-status')).json;
      pane.empty();
      pane.createEl('h3', { text: status.active ? '자동 수집 사용 중' : '자동 수집이 꺼져 있습니다' });
      pane.createEl('p', { text: status.active
        ? 'Ubuntu 사용자 타이머가 앱 종료 여부와 관계없이 24시간마다 추천 후보를 갱신합니다.'
        : '이 운영체제에서는 독립 백그라운드 수집을 사용할 수 없거나 타이머가 아직 활성화되지 않았습니다.' });
      pane.createEl('code', { text: 'paper-scholar-crawl.timer' });
    } catch (error) { pane.empty(); pane.createDiv('paper-error').setText(error.message); }
  }
}

class ExplanationPopup {
  constructor(view, options) {
    this.view = view;
    this.plugin = view.plugin;
    this.options = options;
    this.id = crypto.randomUUID();
    this.chatSessionId = `explain:${options.docId}:${this.id}`;
    this.history = [];
    this.streaming = false;
    this.el = null;
    this.connector = null;
    this.updateBound = () => this.updateConnector();
  }

  savedSize() {
    const fallback = { width: 500, height: 540 };
    try {
      const parsed = JSON.parse(localStorage.getItem('paper-research-explanation-size') || 'null');
      if (Number.isFinite(parsed?.width) && Number.isFinite(parsed?.height)) return parsed;
    } catch (_) {}
    return fallback;
  }

  open() {
    const size = this.savedSize();
    const anchor = this.options.anchor?.getBoundingClientRect?.() || { left: window.innerWidth * .45, top: 120, right: window.innerWidth * .45, bottom: 140, width: 0, height: 20 };
    const width = Math.min(Math.max(360, size.width), window.innerWidth - 24);
    const height = Math.min(Math.max(320, size.height), window.innerHeight - 24);
    const rightCandidate = anchor.right + 32;
    const left = rightCandidate + width < window.innerWidth ? rightCandidate : Math.max(12, anchor.left - width - 32);
    const top = Math.max(12, Math.min(window.innerHeight - height - 12, anchor.top - 24));

    const el = document.createElement('section');
    el.className = 'paper-explanation-popup';
    el.style.cssText = `left:${left}px;top:${top}px;width:${width}px;height:${height}px`;
    el.innerHTML = `
      <header class="paper-explanation-header">
        <strong>✦ 설명</strong><span>${String(this.options.label || '').replace(/[<>]/g, '')}</span>
        <button type="button" class="paper-explanation-close" aria-label="닫기">×</button>
      </header>
      ${this.options.imageDataUrl ? `<img class="paper-explanation-image" alt="설명 대상" src="${this.options.imageDataUrl}">` : ''}
      <div class="paper-explanation-messages"></div>
      <form class="paper-explanation-form"><textarea rows="2" placeholder="이 설명에 대해 질문하세요"></textarea><button type="submit">보내기</button></form>
      ${['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'].map((direction) => `<i class="paper-resize-handle is-${direction}" data-direction="${direction}"></i>`).join('')}
    `;
    document.body.appendChild(el);
    this.el = el;
    this.messagesEl = el.querySelector('.paper-explanation-messages');
    this.input = el.querySelector('textarea');
    this.sendButton = el.querySelector('.paper-explanation-form button');
    el.querySelector('.paper-explanation-close').onclick = () => this.close();
    el.querySelector('.paper-explanation-form').onsubmit = (event) => {
      event.preventDefault();
      const question = this.input.value.trim();
      if (!question) return;
      this.input.value = '';
      void this.send(question, false);
    };
    this.input.onkeydown = (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        el.querySelector('.paper-explanation-form').requestSubmit();
      }
    };
    this.bindDrag(el.querySelector('.paper-explanation-header'));
    el.querySelectorAll('.paper-resize-handle').forEach((handle) => this.bindResize(handle));
    this.createConnector();
    window.addEventListener('resize', this.updateBound);
    document.addEventListener('scroll', this.updateBound, true);
    this.view.explanationPopups.add(this);
    this.options.anchor?.addClass?.('is-explanation-active');
    const kind = this.options.kind === 'figure' ? 'Figure' : this.options.kind === 'table' ? 'Table' : '섹션';
    const initial = `[설명 대상: ${kind} / ${this.options.label || ''}${this.options.page ? ` / Page ${this.options.page}` : ''}]\n${this.options.context || ''}\n\n[요청]\n논문의 전체 맥락을 고려해 이 부분의 목적, 핵심 아이디어, 앞뒤 내용과의 관계를 한국어로 설명해줘.`;
    void this.send(initial, true);
    return this;
  }

  createConnector() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.classList.add('paper-explanation-connector');
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'var(--interactive-accent)');
    line.setAttribute('stroke-width', '1.5'); line.setAttribute('stroke-dasharray', '5 5');
    svg.appendChild(line); document.body.appendChild(svg);
    this.connector = svg; this.connectorPath = line; this.updateConnector();
  }

  updateConnector() {
    if (!this.el || !this.options.anchor?.isConnected || !this.connectorPath) return;
    const anchor = this.options.anchor.getBoundingClientRect(); const target = this.el.getBoundingClientRect();
    const x1 = anchor.left + anchor.width / 2; const y1 = anchor.top + anchor.height / 2;
    const x2 = x1 < target.left ? target.left : x1 > target.right ? target.right : Math.max(target.left, Math.min(target.right, x1));
    const y2 = y1 < target.top ? target.top : y1 > target.bottom ? target.bottom : Math.max(target.top, Math.min(target.bottom, y1));
    const bend = x1 + (x2 - x1) * .5;
    this.connectorPath.setAttribute('d', `M ${x1} ${y1} C ${bend} ${y1}, ${bend} ${y2}, ${x2} ${y2}`);
  }

  bindDrag(handle) {
    handle.addEventListener('mousedown', (event) => {
      if (event.target.closest('button')) return;
      event.preventDefault();
      const startX = event.clientX; const startY = event.clientY;
      const rect = this.el.getBoundingClientRect();
      const move = (next) => {
        this.el.style.left = `${Math.max(6, Math.min(window.innerWidth - rect.width - 6, rect.left + next.clientX - startX))}px`;
        this.el.style.top = `${Math.max(6, Math.min(window.innerHeight - rect.height - 6, rect.top + next.clientY - startY))}px`;
        this.updateConnector();
      };
      const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    });
  }

  bindResize(handle) {
    handle.addEventListener('mousedown', (event) => {
      event.preventDefault(); event.stopPropagation();
      const direction = handle.dataset.direction; const startX = event.clientX; const startY = event.clientY;
      const rect = this.el.getBoundingClientRect(); const right = rect.right; const bottom = rect.bottom;
      const move = (next) => {
        const dx = next.clientX - startX; const dy = next.clientY - startY;
        let left = rect.left; let top = rect.top; let width = rect.width; let height = rect.height;
        if (direction.includes('e')) width = rect.width + dx;
        if (direction.includes('s')) height = rect.height + dy;
        if (direction.includes('w')) { left = rect.left + dx; width = right - left; }
        if (direction.includes('n')) { top = rect.top + dy; height = bottom - top; }
        if (width < 360) { if (direction.includes('w')) left = right - 360; width = 360; }
        if (height < 320) { if (direction.includes('n')) top = bottom - 320; height = 320; }
        left = Math.max(6, left); top = Math.max(6, top);
        width = Math.min(width, window.innerWidth - left - 6); height = Math.min(height, window.innerHeight - top - 6);
        Object.assign(this.el.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
        this.updateConnector();
      };
      const up = () => {
        localStorage.setItem('paper-research-explanation-size', JSON.stringify({ width: this.el.offsetWidth, height: this.el.offsetHeight }));
        document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
      };
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    });
  }

  appendMessage(role, content) {
    const row = document.createElement('div'); row.className = `paper-explanation-message is-${role}`;
    const bubble = document.createElement('div'); bubble.textContent = content; row.appendChild(bubble); this.messagesEl.appendChild(row);
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    return bubble;
  }

  async send(question, hidden) {
    if (this.streaming) return;
    this.streaming = true; this.input.disabled = true; this.sendButton.disabled = true;
    this.history.push({ role: 'user', content: question });
    if (!hidden) this.appendMessage('user', question);
    const bubble = this.appendMessage('assistant', '설명하는 중…');
    try {
      const response = await this.plugin.api('/api/chat/stream', { method: 'POST', json: {
        session_id: this.options.docId,
        chat_session_id: this.chatSessionId,
        hidden_user_message: hidden,
        image_base64: this.options.imageDataUrl?.replace(/^data:image\/\w+;base64,/, '') || null,
        messages: this.history,
      } });
      const answer = response.text.trim(); this.history.push({ role: 'assistant', content: answer });
      bubble.empty(); await MarkdownRenderer.render(this.view.app, answer || '설명을 생성하지 못했습니다.', bubble, '', this.view);
    } catch (error) { bubble.textContent = `설명 실패: ${error.message}`; }
    finally { this.streaming = false; this.input.disabled = false; this.sendButton.disabled = false; this.updateConnector(); }
  }

  close() {
    window.removeEventListener('resize', this.updateBound);
    document.removeEventListener('scroll', this.updateBound, true);
    this.el?.remove(); this.connector?.remove(); this.view.explanationPopups.delete(this);
    const stillUsed = [...this.view.explanationPopups].some((popup) => popup.options.anchor === this.options.anchor);
    if (!stillUsed) this.options.anchor?.removeClass?.('is-explanation-active');
  }
}

class ResearchWorkspaceView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.route = 'library';
    this.bodyEl = null;
    this.pdfUrl = null;
    this.readerPage = 1;
    this.chatMessages = [];
    this.pdfDocument = null;
    this.pdfRenderTask = null;
    this.pdfRenderTasks = new Map();
    this.pdfPageStates = new Map();
    this.translationDataByPage = new Map();
    this.translationPageEls = new Map();
    this.readerRenderGeneration = 0;
    this.readerScrollTimer = null;
    this.pdfOutline = [];
    this.documentImages = [];
    this.referenceData = null;
    this.citationContextCache = new Map();
    this.explanationPopups = new Set();
    this.selectionToolbar = null;
    this.translationMonitorTimer = null;
    this.translationMonitorGeneration = 0;
    this.translationStatusEl = null;
    this.aiSettingsButton = null;
  }
  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return '논문 연구'; }
  getIcon() { return PAPER_RESEARCH_ICON; }

  async onOpen() { await this.navigate('library'); }

  releasePdf() {
    this.readerRenderGeneration += 1;
    this.translationMonitorGeneration += 1;
    if (this.readerScrollTimer) window.clearTimeout(this.readerScrollTimer);
    if (this.translationMonitorTimer) window.clearTimeout(this.translationMonitorTimer);
    this.translationMonitorTimer = null;
    this.translationStatusEl = null;
    if (this.pdfUrl) URL.revokeObjectURL(this.pdfUrl);
    this.pdfUrl = null;
    try { this.pdfRenderTask?.cancel(); } catch (_) {}
    for (const task of this.pdfRenderTasks.values()) { try { task?.cancel(); } catch (_) {} }
    this.pdfRenderTask = null;
    this.pdfRenderTasks.clear();
    this.pdfPageStates.clear();
    this.translationDataByPage.clear();
    this.translationPageEls.clear();
    try { this.pdfDocument?.destroy(); } catch (_) {}
    this.pdfDocument = null;
    this.citationContextCache.clear();
    this.removeSelectionToolbar();
    for (const popup of [...this.explanationPopups]) popup.close();
  }

  buildShell(active) {
    this.releasePdf();
    const root = this.contentEl;
    root.empty();
    root.addClass('paper-workspace-root');
    const toolbar = root.createDiv('paper-workspace-toolbar');
    [['library', '보관함'], ['scholar', 'Scholar'], ['research', '연구 탐색'], ['history', '히스토리'], ['chats', '채팅'], ['vocabulary', '단어장']]
      .forEach(([route, label]) => {
        const button = toolbar.createEl('button', { text: label, cls: route === active ? 'is-active' : '' });
        button.onclick = () => void this.navigate(route);
      });
    toolbar.createDiv('paper-workspace-spacer');
    this.aiSettingsButton = toolbar.createEl('button', { text: 'AI 설정' });
    this.aiSettingsButton.onclick = () => new AISettingsModal(this.app, this.plugin, this).open();
    void this.refreshAIButtonLabel();
    toolbar.createEl('button', { text: 'PDF 가져오기', cls: 'mod-cta' }).onclick = () => void this.plugin.importActivePdf();
    toolbar.createEl('button', { text: '새로고침' }).onclick = () => void this.navigate(this.route, true);
    this.bodyEl = root.createDiv('paper-native-body');
    return this.bodyEl;
  }

  async refreshAIButtonLabel() {
    const button = this.aiSettingsButton;
    if (!button?.isConnected) return;
    try {
      const settings = (await this.plugin.api('/api/settings/system')).json;
      if (button !== this.aiSettingsButton || !button.isConnected) return;
      const provider = aiProviderConfig(settings.trans_provider);
      const parsed = splitAIModel(settings.trans_provider, settings.trans_model);
      const modelLabel = provider.models.find(([value]) => value === parsed.model)?.[1] || parsed.model || '모델 선택';
      button.setText(`AI · ${modelLabel}${parsed.effort ? ` · ${parsed.effort}` : ''}`);
      button.setAttribute('aria-label', 'AI 모델 설정');
      button.title = `번역: ${provider.label} / ${settings.trans_model || ''}`;
    } catch (_) {
      button.setText('AI 설정');
    }
  }

  loading(text = '불러오는 중…') {
    this.bodyEl.empty();
    this.bodyEl.createDiv('paper-loading').setText(text);
  }

  fail(error) {
    this.bodyEl.empty();
    const box = this.bodyEl.createDiv('paper-error');
    box.createEl('h3', { text: '표시하지 못했습니다' });
    box.createEl('p', { text: error.message || String(error) });
    box.createEl('button', { text: '다시 시도' }).onclick = () => void this.navigate(this.route, true);
  }

  async navigate(route = 'library', force = false) {
    const normalized = String(route).replace(/^#/, '');
    const reader = normalized.match(/^viewer\?id=([^&]+)/);
    const note = normalized.match(/^note\?id=([^&]+)/);
    this.route = normalized;
    if (reader) {
      const params = new URLSearchParams(normalized.slice(normalized.indexOf('?') + 1));
      return this.renderReader(decodeURIComponent(reader[1]), force, { page: Number(params.get('page')) || null, side: params.get('side') || 'translation' });
    }
    if (note) return this.renderNote(decodeURIComponent(note[1]));
    this.plugin.currentDocId = null;
    this.buildShell(normalized === 'trash' || normalized === 'notes' ? 'library' : normalized);
    this.loading(force ? '연결을 확인하고 새로고침하는 중…' : '불러오는 중…');
    try {
      await this.plugin.ensureConnection(force);
      if (normalized === 'scholar') await this.renderScholar();
      else if (normalized === 'research') await this.renderResearch();
      else if (normalized === 'history') await this.renderHistory();
      else if (normalized === 'chats') await this.renderChats();
      else if (normalized === 'trash') await this.renderTrash();
      else if (normalized === 'notes') await this.renderNotes();
      else if (normalized === 'vocabulary') await this.renderVocabulary();
      else await this.renderLibrary();
    } catch (error) { this.fail(error); }
  }

  async renderLibrary(query = '', folderId = '') {
    const endpoint = query.trim() ? `/api/library/search?q=${encodeURIComponent(query.trim())}` : '/api/library';
    const [documentsResponse, foldersResponse, bookmarksResponse] = await Promise.all([
      this.plugin.api(endpoint),
      this.plugin.api('/api/library/folders'),
      this.plugin.api('/api/scholar/bookmarks'),
    ]);
    const documents = documentsResponse.json.documents || [];
    const folders = foldersResponse.json.folders || [];
    const bookmarks = bookmarksResponse.json.results || [];
    const activeFolderId = String(folderId || '');
    const visible = activeFolderId === 'unfiled'
      ? documents.filter((doc) => !doc.folder_id)
      : activeFolderId
        ? documents.filter((doc) => String(doc.folder_id || '') === activeFolderId)
        : documents;
    this.bodyEl.empty();
    const layout = this.bodyEl.createDiv('paper-library-layout');
    const sidebar = layout.createEl('aside', { cls: 'paper-folder-sidebar', attr: { 'aria-label': '논문 폴더' } });
    const sidebarHeader = sidebar.createDiv('paper-folder-sidebar-header');
    sidebarHeader.createEl('strong', { text: '폴더' });
    const createFolder = async () => {
      const name = window.prompt('새 폴더 이름');
      if (!name?.trim()) return;
      try {
        await this.plugin.api('/api/library/folders', { method: 'POST', json: { name: name.trim() } });
        await this.renderLibrary(query, activeFolderId);
      } catch (error) { new Notice(error.message); }
    };
    sidebarHeader.createEl('button', { text: '+', attr: { 'aria-label': '새 폴더', title: '새 폴더' } }).onclick = () => void createFolder();
    const folderList = sidebar.createDiv('paper-folder-list');
    const addFolderItem = (id, label, count, icon) => {
      const button = folderList.createEl('button', { cls: String(id) === activeFolderId ? 'is-active' : '' });
      button.createSpan({ cls: 'paper-folder-icon', text: icon });
      button.createSpan({ cls: 'paper-folder-name', text: label, attr: { title: label } });
      button.createSpan({ cls: 'paper-folder-count', text: String(count) });
      button.onclick = () => void this.renderLibrary(query, id);
      return button;
    };
    addFolderItem('', '전체 논문', documents.length, '▦');
    addFolderItem('unfiled', '미분류', documents.filter((doc) => !doc.folder_id).length, '○');
    addFolderItem('bookmarks', '북마크', bookmarks.length, '☆');
    folders.forEach((folder) => addFolderItem(
      String(folder.id),
      folder.name,
      documents.filter((doc) => String(doc.folder_id || '') === String(folder.id)).length,
      '▱',
    ));
    const activeFolder = folders.find((folder) => String(folder.id) === activeFolderId);
    if (activeFolder) {
      const folderActions = sidebar.createDiv('paper-folder-sidebar-actions');
      folderActions.createEl('button', { text: '이름 변경' }).onclick = async () => {
        const name = window.prompt('새 폴더 이름', activeFolder.name || '');
        if (!name?.trim()) return;
        await this.plugin.api(`/api/library/folders/${encodeURIComponent(activeFolderId)}`, { method: 'PUT', json: { name: name.trim() } });
        await this.renderLibrary(query, activeFolderId);
      };
      folderActions.createEl('button', { text: '삭제' }).onclick = async () => {
        if (!window.confirm(`'${activeFolder.name}' 폴더를 삭제할까요? 논문은 보관함에 남습니다.`)) return;
        await this.plugin.api(`/api/library/folders/${encodeURIComponent(activeFolderId)}`, { method: 'DELETE' });
        await this.renderLibrary(query, '');
      };
    }

    const main = layout.createEl('section', { cls: 'paper-library-main' });
    const viewMode = localStorage.getItem('paper-research-library-view') === 'list' ? 'list' : 'grid';
    const header = main.createDiv('paper-page-header');
    const heading = header.createDiv();
    const bookmarkQuery = query.trim().toLocaleLowerCase();
    const visibleBookmarks = bookmarks.filter((paper) => !bookmarkQuery || [
      paper.title, paper.abstract, paper.venue, plainAuthors(paper.authors),
    ].join(' ').toLocaleLowerCase().includes(bookmarkQuery));
    const isBookmarks = activeFolderId === 'bookmarks';
    heading.createEl('h2', { text: isBookmarks ? '북마크' : (activeFolder?.name || (activeFolderId === 'unfiled' ? '미분류' : '보관함')) });
    heading.createEl('p', { text: `${isBookmarks ? visibleBookmarks.length : visible.length}편의 논문` });
    const headerActions = header.createDiv('paper-card-actions');
    const viewToggle = isBookmarks ? null : headerActions.createDiv('paper-library-view-toggle');
    const setViewMode = (mode) => {
      localStorage.setItem('paper-research-library-view', mode);
      void this.renderLibrary(query, activeFolderId);
    };
    if (viewToggle) {
      viewToggle.createEl('button', {
        text: '▦ 격자', cls: viewMode === 'grid' ? 'is-active' : '', attr: { 'aria-label': '격자 보기' },
      }).onclick = () => setViewMode('grid');
      viewToggle.createEl('button', {
        text: '☰ 목록', cls: viewMode === 'list' ? 'is-active' : '', attr: { 'aria-label': '목록 보기' },
      }).onclick = () => setViewMode('list');
    }
    headerActions.createEl('button', { text: '노트 동기화' }).onclick = () => void this.plugin.syncAllNotes();
    headerActions.createEl('button', { text: '휴지통' }).onclick = () => void this.navigate('trash');
    const controls = main.createDiv('paper-filter-row');
    const search = controls.createEl('input', { type: 'search', placeholder: '제목, 분야, 번역 본문 검색', value: query });
    const run = () => void this.renderLibrary(search.value, activeFolderId);
    search.addEventListener('keydown', (event) => { if (event.key === 'Enter') run(); });
    controls.createEl('button', { text: '검색' }).onclick = run;
    if (query) controls.createEl('button', { text: '검색 지우기' }).onclick = () => void this.renderLibrary('', activeFolderId);
    if (isBookmarks) {
      const bookmarked = main.createDiv('paper-search-results');
      this.renderScholarResults(bookmarked, { results: visibleBookmarks }, folders);
      return;
    }
    const dropZone = main.createDiv('paper-pdf-drop-zone');
    dropZone.createEl('strong', { text: 'PDF를 여기에 놓으세요' });
    dropZone.createSpan({ text: '파일 관리자에서 한 편 또는 여러 편을 바로 가져올 수 있습니다.' });
    dropZone.addEventListener('dragover', (event) => { event.preventDefault(); dropZone.addClass('is-dragging'); });
    dropZone.addEventListener('dragleave', () => dropZone.removeClass('is-dragging'));
    dropZone.addEventListener('drop', async (event) => {
      event.preventDefault(); dropZone.removeClass('is-dragging');
      const files = await this.plugin.pdfFilesFromDrop(event.dataTransfer);
      if (!files.length) { new Notice('드롭한 항목에서 PDF를 찾지 못했습니다.'); return; }
      let completed = 0;
      for (const file of files) {
        try { await this.plugin.importExternalPdf(file, false); completed += 1; }
        catch (error) { new Notice(`${file.name}: ${error.message}`); }
      }
      new Notice(`${completed}개 PDF를 보관함에 가져왔습니다.`);
      await this.renderLibrary(search.value, activeFolderId);
    });
    const grid = main.createDiv(`paper-card-grid is-${viewMode}`);
    if (!visible.length) grid.createDiv('paper-empty').setText(query ? '검색 결과가 없습니다.' : '이 폴더에 논문이 없습니다.');
    visible.forEach((doc) => this.renderPaperCard(grid, doc, folders, () => this.renderLibrary(query, activeFolderId)));
  }

  async renderTrash() {
    const documents = (await this.plugin.api('/api/library/trash')).json.documents || [];
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const title = header.createDiv(); title.createEl('h2', { text: '휴지통' }); title.createEl('p', { text: `${documents.length}편` });
    const actions = header.createDiv('paper-card-actions');
    actions.createEl('button', { text: '← 보관함' }).onclick = () => void this.navigate('library');
    const empty = actions.createEl('button', { text: '휴지통 비우기' }); empty.disabled = !documents.length;
    empty.onclick = async () => {
      if (!window.confirm('휴지통의 모든 논문을 영구 삭제할까요? 이 작업은 되돌릴 수 없습니다.')) return;
      await this.plugin.api('/api/library/trash/empty', { method: 'DELETE' }); await this.renderTrash();
    };
    const grid = this.bodyEl.createDiv('paper-card-grid');
    documents.forEach((doc) => {
      const card = grid.createDiv('paper-card'); card.createEl('h3', { text: paperTitle(doc) });
      card.createEl('p', { cls: 'paper-card-meta', text: [plainAuthors(doc.metadata?.authors), doc.metadata?.year].filter(Boolean).join(' · ') });
      const buttons = card.createDiv('paper-card-actions');
      buttons.createEl('button', { text: '복원', cls: 'mod-cta' }).onclick = async () => { await this.plugin.api(`/api/library/${encodeURIComponent(doc.id)}/restore`, { method: 'POST' }); await this.renderTrash(); };
      buttons.createEl('button', { text: '영구 삭제' }).onclick = async () => {
        if (!window.confirm(`'${paperTitle(doc)}'을(를) 영구 삭제할까요?`)) return;
        await this.plugin.api(`/api/library/${encodeURIComponent(doc.id)}/permanent`, { method: 'DELETE' }); await this.renderTrash();
      };
    });
    if (!documents.length) grid.createDiv('paper-empty').setText('휴지통이 비어 있습니다.');
  }

  renderPaperCard(parent, doc, folders, onChanged = null) {
    const card = parent.createDiv('paper-card');
    card.createEl('h3', { text: paperTitle(doc) });
    const meta = doc.metadata || {};
    card.createEl('p', { cls: 'paper-card-meta', text: [plainAuthors(meta.authors), meta.year, meta.venue].filter(Boolean).join(' · ') });
    const translated = doc.translated_pages?.length || 0;
    card.createEl('p', { text: `${doc.total_pages || 1}쪽 · 번역 ${translated}쪽` });
    const tags = card.createDiv('paper-tags');
    asList(meta.categories).slice(0, 4).forEach((tag) => tags.createSpan({ text: tag }));
    const actions = card.createDiv('paper-card-actions');
    actions.createEl('button', { text: '읽기', cls: 'mod-cta' }).onclick = () => void this.navigate(`viewer?id=${encodeURIComponent(doc.id)}`);
    actions.createEl('button', { text: '노트' }).onclick = () => void this.navigate(`note?id=${encodeURIComponent(doc.id)}`);
    actions.createEl('button', { text: 'Vault 저장' }).onclick = () => void this.plugin.exportNote(doc.id).catch((error) => new Notice(error.message));
    actions.createEl('button', { text: '휴지통' }).onclick = async () => {
      if (!window.confirm(`'${paperTitle(doc)}'을(를) 휴지통으로 이동할까요?`)) return;
      await this.plugin.api(`/api/library/${encodeURIComponent(doc.id)}`, { method: 'DELETE' });
      if (onChanged) await onChanged(); else await this.renderLibrary();
    };
    const folder = actions.createEl('select', { attr: { 'aria-label': '폴더 이동' } });
    folder.createEl('option', { text: '미분류', value: '' });
    folders.forEach((item) => folder.createEl('option', { text: item.name, value: String(item.id) }));
    folder.value = String(doc.folder_id || '');
    folder.onchange = async () => {
      await this.plugin.api(`/api/library/${encodeURIComponent(doc.id)}/folder`, {
        method: 'PUT', json: { folder_id: folder.value ? Number(folder.value) : null },
      });
      new Notice('폴더를 변경했습니다.');
      if (onChanged) await onChanged();
    };
  }

  async renderScholar() {
    const folders = (await this.plugin.api('/api/library/folders')).json.folders || [];
    try {
      const session = (await this.plugin.api('/api/scholar/session', { method: 'POST', json: {} })).json;
      this.scholarPreviousVisitAt = session.previous_visit_at || '';
    } catch (_) { this.scholarPreviousVisitAt = ''; }
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const title = header.createDiv();
    title.createEl('h2', { text: 'Scholar' });
    title.createEl('p', { text: '연구 의도를 해석해 학술 레코드를 검색합니다.' });
    const form = this.bodyEl.createDiv('paper-scholar-form');
    const query = form.createEl('textarea', { placeholder: '예: 가우시안을 사용하는 occupancy forecasting 모델' });
    const filters = form.createDiv('paper-filter-row');
    const sort = filters.createEl('select');
    [['relevance', '관련성순'], ['newest', '최신순'], ['cited', '인용순']].forEach(([value, label]) => sort.createEl('option', { value, text: label }));
    const openAccessLabel = filters.createEl('label', { text: ' 공개 PDF만' });
    const openAccess = openAccessLabel.createEl('input', { type: 'checkbox' });
    let results = null;
    const search = async () => {
      if (query.value.trim().length < 2) return new Notice('검색 문장을 입력하세요.');
      results.empty(); results.createDiv('paper-loading').setText('검색 의도를 분석하고 논문을 찾는 중…');
      try {
        const data = (await this.plugin.api('/api/paper-search', { method: 'POST', json: {
          query: query.value.trim(), sort: sort.value, open_access: openAccess.checked,
        } })).json;
        this.renderScholarResults(results, data, folders);
      } catch (error) { results.empty(); results.createDiv('paper-error').setText(error.message); }
    };
    this.scholarQuery = query;
    this.scholarSearch = search;
    filters.createEl('button', { text: '검색', cls: 'mod-cta' }).onclick = () => void search();
    const feedBar = this.bodyEl.createDiv('paper-scholar-feed-bar');
    const folderLabel = feedBar.createEl('label');
    folderLabel.createSpan({ text: '추천 기준' });
    const folderSelect = folderLabel.createEl('select', { attr: { 'aria-label': '추천 기준 폴더' } });
    folderSelect.createEl('option', { value: '', text: '전체 보관함' });
    folders.forEach((folder) => folderSelect.createEl('option', {
      value: String(folder.id),
      text: `${folder.name}${Number.isFinite(Number(folder.document_count)) ? ` (${folder.document_count})` : ''}`,
    }));
    const loadFeed = () => {
      const selected = folders.find((folder) => String(folder.id) === folderSelect.value);
      void this.loadScholarFeed(results, folderSelect.value, selected?.name || '전체 보관함', folders);
    };
    const crawlButton = feedBar.createEl('button', {
      text: '↻', cls: 'paper-scholar-refresh',
      attr: { title: '추천 논문 새로고침', 'aria-label': '추천 논문 새로고침' },
    });
    const refreshCrawlTitle = async () => {
      try {
        const status = (await this.plugin.api('/api/scholar/crawl/status')).json;
        const crawled = status.last_crawl_at ? new Date(status.last_crawl_at) : null;
        const when = crawled && !Number.isNaN(crawled.getTime())
          ? crawled.toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : '아직 없음';
        crawlButton.title = `추천 논문 새로고침 · 최근 수집 ${when}`;
      } catch (_) { crawlButton.title = '추천 논문 새로고침'; }
    };
    crawlButton.onclick = async () => {
      crawlButton.disabled = true;
      crawlButton.setText('…');
      try {
        await this.plugin.api('/api/scholar/crawl?force=true', { method: 'POST', json: {} });
        await refreshCrawlTitle();
        loadFeed();
      } catch (error) { new Notice(error.message); }
      finally { crawlButton.disabled = false; crawlButton.setText('↻'); }
    };
    void refreshCrawlTitle();
    folderSelect.onchange = () => loadFeed();
    results = this.bodyEl.createDiv('paper-search-results');
    loadFeed();
  }

  async renderResearch() {
    this.bodyEl.empty();
    new ResearchExplorer(this.app, this.plugin).render(this.bodyEl);
  }

  async loadScholarFeed(parent, folderId = '', folderName = '전체 보관함', folders = []) {
    parent.empty(); parent.createDiv('paper-loading').setText('관심 분야를 분석하는 중…');
    const folderQuery = folderId ? `&folder_id=${encodeURIComponent(folderId)}` : '';
    try {
      const endpoint = `/api/scholar/feed?mode=recommended${folderQuery}`;
      this.renderScholarResults(parent, (await this.plugin.api(endpoint)).json, folders, folderId);
      const context = document.createElement('div');
      context.className = 'paper-scholar-feed-context';
      context.textContent = `${folderName} 추천 논문`;
      parent.prepend(context);
    }
    catch (error) { parent.empty(); parent.createDiv('paper-error').setText(error.message); }
  }

  renderScholarResults(parent, data, folders = [], defaultFolderId = '') {
    parent.empty();
    if (data.interpretation) parent.createDiv('paper-search-answer').setText(`검색 해석: ${data.interpretation}`);
    if (data.answer) parent.createDiv('paper-search-answer').setText(data.answer);
    const grid = parent.createDiv('paper-card-grid paper-scholar-result-list');
    (data.results || []).forEach((paper) => {
      const card = grid.createDiv('paper-card paper-scholar-card');
      card.createEl('h3', { text: paper.title || '제목 없음' });
      const citationCount = paper.citation_count ?? paper.cited_by_count;
      card.createEl('p', { cls: 'paper-card-meta', text: [plainAuthors(paper.authors), paper.year, paper.venue, paper.source, Number.isFinite(Number(citationCount)) ? `인용 ${Number(citationCount).toLocaleString()}` : ''].filter(Boolean).join(' · ') });
      if (paper.relevance) {
        const relevance = card.createDiv('paper-scholar-relevance');
        relevance.createEl('strong', { text: '왜 관련 있나요?' });
        relevance.createEl('p', { text: paper.relevance });
      }
      if (paper.highlight) {
        const highlight = card.createEl('blockquote', { cls: 'paper-scholar-highlight' });
        highlight.createEl('span', { text: '핵심 문장' });
        highlight.createEl('p', { text: paper.highlight });
      }
      if (paper.abstract) {
        const details = card.createEl('details'); details.createEl('summary', { text: '초록' }); details.createEl('p', { text: paper.abstract });
      }
      const actions = card.createDiv('paper-card-actions');
      if (paper.url) {
        const original = actions.createEl('a', { text: '원문 정보', href: paper.url, attr: { target: '_blank', rel: 'noopener' } });
        original.onclick = () => void this.recordScholarInteraction(paper, 'open');
      }
      if (paper.pdf_url) {
        const pdf = actions.createEl('a', { text: 'PDF 열기', href: paper.pdf_url, attr: { target: '_blank', rel: 'noopener' } });
        pdf.onclick = () => void this.recordScholarInteraction(paper, 'open');
      }

      const similar = actions.createEl('button', { text: '유사 논문' });
      similar.onclick = () => void this.searchScholarSimilar(paper, parent, folders, defaultFolderId);
      const bibtex = actions.createEl('button', { text: 'BibTeX' });
      bibtex.onclick = () => void this.copyScholarBibtex(paper);

      [[1, '👍', '관심 있음'], [-1, '👎', '관심 없음']].forEach(([rating, label, title]) => {
        const button = actions.createEl('button', {
          text: label,
          cls: `paper-scholar-rating${Number(paper.rating) === rating ? ' is-active' : ''}`,
          attr: { title, 'aria-label': title },
        });
        button.dataset.rating = String(rating);
        button.onclick = () => void this.rateScholarPaper(paper, rating, card);
      });

      const bookmark = actions.createEl('button', {
        text: paper.bookmarked ? '★' : '☆',
        cls: paper.bookmarked ? 'paper-scholar-bookmark is-active' : 'paper-scholar-bookmark',
        attr: { title: paper.bookmarked ? '북마크 해제' : '북마크', 'aria-label': paper.bookmarked ? '북마크 해제' : '북마크' },
      });
      const hide = actions.createEl('button', { text: '숨김', attr: { title: '다음 추천에서 제외' } });
      hide.onclick = async () => {
        await this.recordScholarInteraction(paper, 'hide');
        card.remove();
        new Notice('이 논문을 다음 추천에서 숨깁니다.');
      };

      const preview = card.createDiv('paper-scholar-preview is-hidden');
      if (paper.pdf_url) {
        const previewButton = actions.createEl('button', { text: '그림·표 미리보기' });
        previewButton.onclick = () => void this.toggleScholarPreview(paper, preview, previewButton);
      }

      const saveRow = card.createDiv('paper-scholar-save-row');
      const folderSelect = saveRow.createEl('select', { attr: { 'aria-label': '저장할 폴더' } });
      folderSelect.createEl('option', { value: '', text: '미분류' });
      folders.forEach((folder) => folderSelect.createEl('option', { value: String(folder.id), text: folder.name }));
      folderSelect.value = String(paper.bookmark_folder_id ?? defaultFolderId ?? '');
      bookmark.onclick = () => void this.toggleScholarBookmark(paper, folderSelect, bookmark);
      const savedDocId = paper.saved_document_id || '';
      const save = saveRow.createEl('button', {
        text: savedDocId ? '보관함에서 열기' : (paper.pdf_url ? '선택한 폴더에 저장' : '공개 원문 찾기'),
        cls: 'mod-cta',
      });
      if (savedDocId) save.dataset.docId = String(savedDocId);
      save.onclick = async () => {
        if (save.dataset.docId) {
          await this.navigate(`viewer?id=${encodeURIComponent(save.dataset.docId)}`);
          return;
        }
        save.disabled = true; folderSelect.disabled = true; save.setText('받는 중…');
        try {
          if (!paper.pdf_url) {
            save.setText('공개 저장소 확인 중…');
            const resolved = (await this.plugin.api('/api/scholar/resolve-pdf', {
              method: 'POST', json: { paper },
            })).json;
            if (!resolved.pdf_url) throw new Error('공개 원문을 찾지 못했습니다. 북마크로 보관할 수 있습니다.');
            paper.pdf_url = resolved.pdf_url;
            new Notice(`${resolved.source || '공개 저장소'}에서 원문을 찾았습니다.`);
            save.setText('받는 중…');
          }
          const result = (await this.plugin.api('/api/scholar/import', { method: 'POST', json: {
            paper_id: String(paper.id), title: paper.title, pdf_url: paper.pdf_url, url: paper.url || '', doi: paper.doi || '',
            authors: asList(paper.authors), year: paper.year || null, venue: paper.venue || '',
            folder_id: folderSelect.value ? Number(folderSelect.value) : null,
            semantic_scholar_id: paper.semantic_scholar_id || '',
          } })).json;
          save.dataset.docId = result.session_id;
          paper.downloaded = true; paper.saved_document_id = result.session_id;
          save.setText('보관함에서 열기');
          save.disabled = false;
          new Notice('선택한 폴더에 저장했습니다.');
        } catch (error) {
          new Notice(error.message); save.disabled = false; folderSelect.disabled = false;
          save.setText(paper.pdf_url ? '선택한 폴더에 저장' : '공개 원문 찾기');
        }
      };
    });
    if (!(data.results || []).length) grid.createDiv('paper-empty').setText('표시할 논문이 없습니다.');
  }

  async searchScholarSimilar(paper, parent, folders = [], defaultFolderId = '') {
    parent.empty(); parent.createDiv('paper-loading').setText('전용 추천 모델로 유사 논문을 찾는 중…');
    try {
      const data = (await this.plugin.api('/api/scholar/similar', { method: 'POST', json: { paper, limit: 20 } })).json;
      this.renderScholarResults(parent, data, folders, defaultFolderId);
    } catch (error) { parent.empty(); parent.createDiv('paper-error').setText(error.message); }
  }

  async recordScholarInteraction(paper, action) {
    try {
      await this.plugin.api('/api/scholar/interaction', { method: 'POST', json: { paper_id: String(paper.id), action } });
    } catch (_) {}
  }

  async toggleScholarBookmark(paper, folderSelect, button, forceSave = false) {
    const saved = forceSave || !paper.bookmarked;
    try {
      const result = (await this.plugin.api('/api/scholar/bookmark', { method: 'POST', json: {
        paper_id: String(paper.id), paper,
        folder_id: folderSelect.value ? Number(folderSelect.value) : null,
        saved,
      } })).json;
      paper.bookmarked = result.saved;
      paper.bookmark_folder_id = result.folder_id;
      button.setText(result.saved ? '★' : '☆');
      button.toggleClass('is-active', result.saved);
      button.title = result.saved ? '북마크 해제' : '북마크';
      new Notice(result.saved ? '논문을 북마크했습니다.' : '북마크를 해제했습니다.');
    } catch (error) { new Notice(error.message); }
  }

  async copyScholarBibtex(paper) {
    try {
      await navigator.clipboard.writeText(scholarBibtex(paper));
      new Notice('BibTeX를 복사했습니다.');
    } catch (_) { new Notice('BibTeX를 복사하지 못했습니다.'); }
  }

  async rateScholarPaper(paper, rating, card) {
    try {
      const result = (await this.plugin.api('/api/scholar/feedback', { method: 'POST', json: {
        paper_id: String(paper.id), rating, paper,
      } })).json;
      paper.rating = Number(result.rating || 0);
      card.querySelectorAll('.paper-scholar-rating').forEach((button) => {
        button.toggleClass('is-active', Number(button.dataset.rating) === paper.rating);
      });
      new Notice(paper.rating ? '다음 맞춤 추천에 반영됩니다.' : '평가를 취소했습니다.');
    } catch (error) { new Notice(error.message); }
  }

  async toggleScholarPreview(paper, preview, button) {
    if (preview.dataset.loaded === 'true') {
      preview.toggleClass('is-hidden', !preview.hasClass('is-hidden'));
      button.setText(preview.hasClass('is-hidden') ? '그림·표 미리보기' : '미리보기 닫기');
      return;
    }
    preview.removeClass('is-hidden');
    preview.empty(); preview.createDiv('paper-loading').setText('공개 PDF에서 Figure/Table을 찾는 중…');
    button.disabled = true;
    try {
      const data = (await this.plugin.api('/api/scholar/preview', { method: 'POST', json: { pdf_url: paper.pdf_url } })).json;
      preview.empty();
      const visuals = data.visuals || [];
      if (!visuals.length) preview.createDiv('paper-empty').setText('번호가 붙은 Figure/Table을 찾지 못했습니다.');
      visuals.forEach((visual) => {
        const item = preview.createEl('figure');
        item.createEl('img', { attr: { src: visual.image_data, alt: `${visual.label || '시각 자료'} 미리보기` } });
        const caption = item.createEl('figcaption');
        caption.createEl('strong', { text: visual.label || 'Figure/Table' });
        if (visual.page) caption.createSpan({ text: ` · p.${visual.page}` });
        if (visual.caption) caption.createEl('p', { text: visual.caption });
      });
      preview.dataset.loaded = 'true';
      button.setText('미리보기 닫기');
    } catch (error) {
      preview.empty(); preview.createDiv('paper-error').setText(error.message);
    } finally { button.disabled = false; }
  }

  async renderHistory(date = new Date()) {
    const year = date.getFullYear(); const month = date.getMonth() + 1;
    const data = (await this.plugin.api(`/api/library/reading-history?year=${year}&month=${month}`)).json;
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const nav = header.createDiv();
    nav.createEl('h2', { text: `${year}년 ${month}월 읽기 기록` });
    nav.createEl('p', { text: `${data.active_days || 0}일 · ${data.paper_count || 0}편` });
    const buttons = header.createDiv('paper-card-actions');
    buttons.createEl('button', { text: '‹' }).onclick = () => void this.renderHistory(new Date(year, month - 2, 1));
    buttons.createEl('button', { text: '오늘' }).onclick = () => void this.renderHistory(new Date());
    buttons.createEl('button', { text: '›' }).onclick = () => void this.renderHistory(new Date(year, month, 1));
    const firstDay = new Date(year, month - 1, 1).getDay();
    const days = new Date(year, month, 0).getDate();
    const byDay = new Map();
    (data.activities || []).forEach((item) => {
      const day = Number(item.activity_date.slice(-2));
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(item);
    });
    const calendar = this.bodyEl.createDiv('paper-calendar');
    ['일', '월', '화', '수', '목', '금', '토'].forEach((label) => calendar.createDiv('paper-calendar-label').setText(label));
    for (let i = 0; i < firstDay; i += 1) calendar.createDiv('paper-calendar-day is-empty');
    for (let day = 1; day <= days; day += 1) {
      const cell = calendar.createDiv('paper-calendar-day'); cell.createEl('strong', { text: String(day) });
      (byDay.get(day) || []).slice(0, 3).forEach((item) => {
        const link = cell.createEl('button', { text: paperTitle(item), cls: 'paper-calendar-paper' });
        link.onclick = () => void this.navigate(`viewer?id=${encodeURIComponent(item.doc_id)}`);
      });
    }
  }

  async renderChats() {
    const documents = (await this.plugin.api('/api/library')).json.documents || [];
    const histories = await Promise.all(documents.map(async (doc) => {
      try { return { doc, history: (await this.plugin.api(`/api/chat/${encodeURIComponent(doc.id)}/history`)).json.history || [] }; }
      catch (_) { return { doc, history: [] }; }
    }));
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const title = header.createDiv(); title.createEl('h2', { text: '채팅 기록' });
    const active = histories.filter((item) => item.history.length); title.createEl('p', { text: `${active.length}편의 논문과 대화` });
    const grid = this.bodyEl.createDiv('paper-card-grid');
    active.forEach(({ doc, history }) => {
      const card = grid.createDiv('paper-card'); card.createEl('h3', { text: paperTitle(doc) });
      const last = history[history.length - 1]; card.createEl('p', { text: String(last?.content || '').slice(0, 260) });
      const actions = card.createDiv('paper-card-actions');
      actions.createEl('button', { text: '대화 이어가기', cls: 'mod-cta' }).onclick = () => void this.navigate(`viewer?id=${encodeURIComponent(doc.id)}&side=chat`);
    });
    if (!active.length) grid.createDiv('paper-empty').setText('저장된 논문 채팅이 없습니다.');
  }

  async renderNotes() {
    const [notesResponse, documentsResponse] = await Promise.all([this.plugin.api('/api/notes'), this.plugin.api('/api/library')]);
    const notes = notesResponse.json.notes || [];
    const documents = new Map((documentsResponse.json.documents || []).map((doc) => [String(doc.id), doc]));
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const title = header.createDiv(); title.createEl('h2', { text: '논문 노트' }); title.createEl('p', { text: `${notes.length}개의 자동 정리 노트` });
    header.createEl('button', { text: '모두 Vault와 동기화' }).onclick = () => void this.plugin.syncAllNotes();
    const grid = this.bodyEl.createDiv('paper-card-grid');
    notes.forEach((note) => {
      const card = grid.createDiv('paper-card');
      card.createEl('h3', { text: paperTitle(documents.get(String(note.doc_id)) || note) });
      card.createEl('p', { text: note.content?.one_line_summary || (note.status === 'generating' ? '노트를 생성하는 중입니다.' : '아직 생성된 노트가 없습니다.') });
      const actions = card.createDiv('paper-card-actions');
      actions.createEl('button', { text: '열기', cls: 'mod-cta' }).onclick = () => void this.navigate(`note?id=${encodeURIComponent(note.doc_id)}`);
      actions.createEl('button', { text: 'Vault 저장' }).onclick = () => void this.plugin.exportNote(note.doc_id).catch((error) => new Notice(error.message));
    });
  }

  async renderNote(docId) {
    this.route = `note?id=${encodeURIComponent(docId)}`;
    this.plugin.currentDocId = docId;
    this.buildShell('library'); this.loading('노트를 불러오는 중…');
    try {
      const [noteResponse, documentResponse] = await Promise.all([
        this.plugin.api(`/api/notes/${encodeURIComponent(docId)}`),
        this.plugin.api(`/api/library/${encodeURIComponent(docId)}`),
      ]);
      const note = noteResponse.json; const document = documentResponse.json;
      this.bodyEl.empty();
      const header = this.bodyEl.createDiv('paper-page-header');
      const title = header.createDiv(); title.createEl('h2', { text: paperTitle(document) }); title.createEl('p', { text: note.status === 'ready' ? '자동 정리 완료' : `상태: ${note.status}` });
      const actions = header.createDiv('paper-card-actions');
      actions.createEl('button', { text: '논문 읽기' }).onclick = () => void this.navigate(`viewer?id=${encodeURIComponent(docId)}`);
      actions.createEl('button', { text: 'Vault 저장', cls: 'mod-cta' }).onclick = () => void this.plugin.exportNote(docId).catch((error) => new Notice(error.message));
      actions.createEl('button', { text: '다시 생성' }).onclick = async () => { await this.plugin.api(`/api/notes/${encodeURIComponent(docId)}/regenerate`, { method: 'POST', json: {} }); new Notice('노트 생성을 시작했습니다.'); };
      if (!note.content) { this.bodyEl.createDiv('paper-empty').setText('생성된 노트가 없습니다.'); return; }
      const content = note.content;
      const experiments = (content.experiment_flow || []).map((item, index) => `${index + 1}. **가설** ${item.hypothesis || ''}\n   - 방법: ${item.method || ''}\n   - 결과: ${item.result || ''}`).join('\n');
      const glossary = (content.glossary || []).map((item) => `- **${item.term || ''}** — ${item.definition || ''}`).join('\n');
      const markdown = [`> [!abstract] 한 줄 요약\n> ${content.one_line_summary || ''}`, `## 요약\n${content.summary || ''}`, `## 핵심 기여\n${markdownList(content.contributions)}`, `## 방법\n${content.method_summary || ''}`, `## 실험과 결과\n${content.results_summary || ''}`, experiments ? `## 실험 흐름\n${experiments}` : '', `## 한계\n${content.limitations || ''}`, `## 핵심 정리\n${markdownList(content.takeaways)}`, content.keywords?.length ? `## 키워드\n${content.keywords.map((item) => `#${String(item).replace(/^#/, '')}`).join(' · ')}` : '', glossary ? `## 용어집\n${glossary}` : ''].filter(Boolean).join('\n\n');
      const article = this.bodyEl.createDiv('paper-note-article markdown-rendered');
      await MarkdownRenderer.render(this.app, markdown, article, '', this);
      if (content.visuals?.length) {
        const gallery = this.bodyEl.createDiv('paper-visual-grid');
        content.visuals.forEach((visual) => this.renderNoteVisual(gallery, docId, visual));
      }
    } catch (error) { this.fail(error); }
  }

  async renderNoteVisual(parent, docId, visual) {
    const figure = parent.createEl('figure');
    figure.createDiv('paper-loading').setText(`${visual.label || 'Figure/Table'} 불러오는 중…`);
    try {
      const response = await this.plugin.api(`/api/notes/${encodeURIComponent(docId)}/assets/${visual.index}`);
      const dataUrl = `data:image/png;base64,${Buffer.from(response.arrayBuffer).toString('base64')}`;
      figure.empty(); figure.createEl('img', { attr: { src: dataUrl, alt: visual.label || '논문 시각 자료' } });
      const explain = figure.createEl('button', { text: '✦', cls: 'paper-note-visual-explain', attr: { title: `${visual.label || '시각 자료'} 설명`, 'aria-label': `${visual.label || '시각 자료'} 설명` } });
      explain.onclick = () => new ExplanationPopup(this, {
        docId, kind: visual.kind === 'table' ? 'table' : 'figure', label: visual.label,
        page: visual.page, context: visual.caption || '', imageDataUrl: dataUrl, anchor: explain,
      }).open();
      figure.createEl('figcaption', { text: `${visual.label || ''} ${visual.caption || ''}`.trim() });
    } catch (_) { figure.empty(); figure.setText(`${visual.label || '시각 자료'}를 불러오지 못했습니다.`); }
  }

  async renderVocabulary() {
    const [cardsResponse, statusResponse] = await Promise.all([this.plugin.api('/api/vocabulary'), this.plugin.api('/api/vocabulary/anki/status')]);
    const cards = cardsResponse.json.cards || []; const status = statusResponse.json;
    this.bodyEl.empty();
    const header = this.bodyEl.createDiv('paper-page-header');
    const title = header.createDiv(); title.createEl('h2', { text: '논문 단어장' }); title.createEl('p', { text: `${cards.length}개 · Anki ${status.connected ? '연결됨' : '연결 대기'}` });
    const actions = header.createDiv('paper-card-actions');
    actions.createEl('button', { text: '지금 복습', cls: 'mod-cta' }).onclick = () => new ReviewModal(this.app, this.plugin).open();
    actions.createEl('button', { text: 'Anki 동기화' }).onclick = async () => { const result = (await this.plugin.api('/api/vocabulary/sync', { method: 'POST', json: {} })).json; new Notice(`${result.synced || 0}개 카드를 동기화했습니다.`); };
    const table = this.bodyEl.createEl('table', { cls: 'paper-vocab-table' });
    const head = table.createEl('thead').createEl('tr'); ['단어', '뜻', '논문', '상태'].forEach((text) => head.createEl('th', { text }));
    const body = table.createEl('tbody');
    cards.forEach((card) => {
      const row = body.createEl('tr'); row.createEl('td', { text: card.term }); row.createEl('td', { text: card.meaning_ko }); row.createEl('td', { text: card.paper_title || '' }); row.createEl('td', { text: card.anki_status || '' });
    });
  }

  async renderReader(docId, force = false, options = {}) {
    this.route = `viewer?id=${encodeURIComponent(docId)}`;
    this.plugin.currentDocId = docId;
    this.buildShell(''); this.loading(force ? '연결을 확인하는 중…' : '논문을 여는 중…');
    try {
      await this.plugin.ensureConnection(force);
      const [docResponse, pdfResponse] = await Promise.all([this.plugin.api(`/api/library/${encodeURIComponent(docId)}`), this.plugin.api(`/api/library/${encodeURIComponent(docId)}/pdf`)]);
      const doc = docResponse.json; const total = Math.max(1, Number(doc.total_pages || 1));
      this.readerPage = Math.min(total, Math.max(1, Number(options.page || doc.metadata?.last_page || 1)));
      const pdfjs = getPdfJs();
      this.pdfDocument = await pdfjs.getDocument({ data: new Uint8Array(pdfResponse.arrayBuffer) }).promise;
      this.currentReaderDoc = doc;
      this.bodyEl.empty(); this.bodyEl.addClass('paper-reader-body');
      const header = this.bodyEl.createDiv('paper-reader-header');
      header.createEl('button', { text: '← 보관함' }).onclick = () => void this.navigate('library');
      const title = header.createDiv('paper-reader-title'); title.createEl('strong', { text: paperTitle(doc) }); title.createEl('span', { text: plainAuthors(doc.metadata?.authors) });
      const pages = header.createDiv('paper-page-controls');
      pages.createEl('button', { text: '‹' }).onclick = () => void this.setReaderPage(docId, this.readerPage - 1, total);
      const pageInput = pages.createEl('input', { type: 'number', value: String(this.readerPage), attr: { min: '1', max: String(total), 'aria-label': '페이지' } });
      pages.createSpan({ text: `/ ${total}` });
      pageInput.onchange = () => void this.setReaderPage(docId, Number(pageInput.value), total);
      pages.createEl('button', { text: '›' }).onclick = () => void this.setReaderPage(docId, this.readerPage + 1, total);
      this.translationStatusEl = header.createSpan({
        cls: 'paper-translation-job-status',
        text: `번역 ${(doc.translated_pages || []).length}/${total}`,
      });
      header.createEl('button', { text: '노트' }).onclick = () => void this.navigate(`note?id=${encodeURIComponent(docId)}`);
      header.createEl('button', { text: 'Vault 저장' }).onclick = () => void this.plugin.exportNote(docId).catch((error) => new Notice(error.message));
      const split = this.bodyEl.createDiv('paper-reader-split');
      const pdfPane = split.createDiv('paper-pdf-pane');
      const pdfTools = pdfPane.createDiv('paper-native-pdf-tools');
      pdfTools.createSpan({ text: '원문' });
      this.pdfScaleSelect = pdfTools.createEl('select', { attr: { 'aria-label': 'PDF 확대 비율' } });
      [['fit', '너비 맞춤'], ['1', '100%'], ['1.25', '125%'], ['1.5', '150%'], ['2', '200%']].forEach(([value, text]) => this.pdfScaleSelect.createEl('option', { value, text }));
      this.pdfScaleSelect.onchange = () => void this.rerenderPdfDocument(docId, total);
      pdfTools.createSpan({ text: '모든 페이지 이어보기 · 원문과 번역은 독립 스크롤', cls: 'paper-reader-hint' });
      this.pdfViewport = pdfPane.createDiv('paper-native-pdf-viewport');
      const side = split.createDiv('paper-reader-side');
      const tabs = side.createDiv('paper-side-tabs');
      this.sideTabButtons = {};
      [['translation', '번역'], ['outline', '목차'], ['references', '인용논문'], ['chat', '채팅']].forEach(([key, label]) => {
        const button = tabs.createEl('button', { text: label, cls: key === 'translation' ? 'is-active' : '' });
        this.sideTabButtons[key] = button;
        button.onclick = () => void this.selectReaderSideTab(key, docId);
      });
      this.sideContent = side.createDiv('paper-side-content');
      this.activeReaderSideTab = 'translation';
      const auxiliary = await Promise.allSettled([
        this.loadPdfOutline(),
        this.plugin.api(`/api/library/${encodeURIComponent(docId)}/images`),
        this.plugin.api(`/api/library/${encodeURIComponent(docId)}/references`),
        this.plugin.api(`/api/chat/${encodeURIComponent(docId)}/history`),
      ]);
      this.documentImages = auxiliary[1].status === 'fulfilled' ? auxiliary[1].value.json.images || [] : [];
      this.referenceData = auxiliary[2].status === 'fulfilled' ? auxiliary[2].value.json : { references: {}, mentions: {} };
      this.chatMessages = auxiliary[3].status === 'fulfilled' ? auxiliary[3].value.json.history || [] : [];
      const generation = this.readerRenderGeneration;
      await this.setupContinuousReader(docId, total, generation);
      void this.ensureAutomaticTranslation(docId, doc, total);
      if (options.side && options.side !== 'translation') await this.selectReaderSideTab(options.side, docId);
      await this.saveProgress(docId, this.readerPage, total);
    } catch (error) { this.fail(error); }
  }

  setTranslationStatus(text, state = '') {
    if (!this.translationStatusEl?.isConnected) return;
    this.translationStatusEl.setText(text);
    this.translationStatusEl.className = `paper-translation-job-status${state ? ` is-${state}` : ''}`;
  }

  async ensureAutomaticTranslation(docId, doc, total) {
    const translated = new Set((doc.translated_pages || []).map(Number));
    if (translated.size >= total) {
      this.setTranslationStatus(`번역 완료 ${total}/${total}`, 'complete');
      return;
    }

    let job = null;
    try { job = (await this.plugin.api(`/api/jobs/${encodeURIComponent(docId)}/status`)).json; }
    catch (_) {}

    const active = job?.status === 'running';
    if (!active) {
      this.setTranslationStatus(`번역 시작 중 ${translated.size}/${total}`, 'running');
      try {
        job = (await this.plugin.api(`/api/jobs/${encodeURIComponent(docId)}/restart`, {
          method: 'POST',
          json: { target_lang: '한국어', style: 'academic', ignore_math: false, ignore_table: true, ignore_refs: false },
        })).json.job;
        new Notice('번역을 자동으로 시작했습니다.');
      } catch (error) {
        this.setTranslationStatus('번역 시작 실패', 'error');
        new Notice(`자동 번역을 시작하지 못했습니다: ${error.message}`);
        return;
      }
    }
    this.startTranslationMonitor(docId, total, job);
  }

  startTranslationMonitor(docId, total, initialJob = null) {
    const monitorGeneration = ++this.translationMonitorGeneration;
    if (this.translationMonitorTimer) window.clearTimeout(this.translationMonitorTimer);
    const knownComplete = new Set((initialJob?.completed_pages || []).map(Number));

    const tick = async () => {
      if (monitorGeneration !== this.translationMonitorGeneration || this.plugin.currentDocId !== docId) return;
      try {
        const job = (await this.plugin.api(`/api/jobs/${encodeURIComponent(docId)}/status`)).json;
        const completed = new Set((job.completed_pages || []).map(Number));
        for (const page of completed) {
          if (!knownComplete.has(page) || !this.translationDataByPage.has(page)) {
            knownComplete.add(page);
            await this.loadTranslationPage(docId, page, this.readerRenderGeneration);
          }
        }
        const failed = (job.failed_pages || []).length;
        if (job.status === 'completed' && completed.size >= total && failed === 0) {
          this.setTranslationStatus(`번역 완료 ${total}/${total}`, 'complete');
          return;
        }
        if (job.status === 'failed' || job.status === 'cancelled') {
          this.setTranslationStatus(`번역 중단 ${completed.size}/${total}`, 'error');
          return;
        }
        if (job.status === 'completed' && (completed.size < total || failed)) {
          this.setTranslationStatus(`번역 재시도 필요 ${completed.size}/${total}`, 'error');
          return;
        }
        this.setTranslationStatus(`번역 중 ${completed.size}/${total}`, 'running');
      } catch (_) {
        this.setTranslationStatus(`번역 상태 확인 중 ${knownComplete.size}/${total}`, 'running');
      }
      if (monitorGeneration === this.translationMonitorGeneration) {
        this.translationMonitorTimer = window.setTimeout(tick, 2000);
      }
    };
    void tick();
  }

  async setReaderPage(docId, page, total) {
    const targetPage = Math.min(total, Math.max(1, Number(page) || 1));
    this.readerPage = targetPage;
    const input = this.bodyEl.querySelector('.paper-page-controls input'); if (input) input.value = String(targetPage);
    await Promise.allSettled([
      this.renderPdfPage(docId, targetPage, this.readerRenderGeneration),
      this.loadTranslationPage(docId, targetPage, this.readerRenderGeneration),
    ]);
    this.readerPage = targetPage; if (input) input.value = String(targetPage);
    this.scrollReaderPaneToPage(this.pdfViewport, '.paper-pdf-page-slot', targetPage);
    if (this.activeReaderSideTab === 'translation') this.scrollReaderPaneToPage(this.sideContent, '.paper-translation-page', targetPage);
    await this.saveProgress(docId, targetPage, total);
  }

  pageRenderOrder(total, start) {
    return Array.from({ length: total }, (_, index) => index + 1).sort((a, b) => Math.abs(a - start) - Math.abs(b - start));
  }

  async setupContinuousReader(docId, total, generation) {
    this.pdfViewport.empty();
    this.pdfDocumentEl = this.pdfViewport.createDiv('paper-continuous-pdf-document');
    const samplePage = await this.pdfDocument.getPage(this.readerPage);
    const sampleBase = samplePage.getViewport({ scale: 1 });
    const available = Math.max(320, this.pdfViewport.clientWidth - 70);
    const selected = this.pdfScaleSelect?.value || 'fit';
    const sampleScale = selected === 'fit' ? Math.min(2.2, available / sampleBase.width) : Number(selected);
    const expectedPageHeight = Math.ceil(sampleBase.height * sampleScale + 42);
    for (let page = 1; page <= total; page += 1) {
      const slot = this.pdfDocumentEl.createDiv('paper-pdf-page-slot'); slot.dataset.page = String(page);
      slot.style.minHeight = `${expectedPageHeight}px`;
      slot.createDiv('paper-continuous-page-label').setText(`${page} / ${total}`);
      const content = slot.createDiv('paper-pdf-page-content'); content.createDiv('paper-loading').setText(`${page}쪽 원문 준비 중…`);
    }
    this.mountTranslationDocument(docId, total);
    this.bindContinuousPageTracking(this.pdfViewport, '.paper-pdf-page-slot', docId, total);
    await Promise.allSettled([
      this.renderPdfPage(docId, this.readerPage, generation),
      this.loadTranslationPage(docId, this.readerPage, generation),
    ]);
    if (generation !== this.readerRenderGeneration) return;
    this.scrollReaderPaneToPage(this.pdfViewport, '.paper-pdf-page-slot', this.readerPage, false);
    this.scrollReaderPaneToPage(this.sideContent, '.paper-translation-page', this.readerPage, false);
    void this.renderRemainingReaderPages(docId, total, generation);
  }

  async renderRemainingReaderPages(docId, total, generation) {
    for (const pageNum of this.pageRenderOrder(total, this.readerPage)) {
      if (generation !== this.readerRenderGeneration || !this.pdfDocument) return;
      if (pageNum === this.readerPage && this.pdfPageStates.has(pageNum) && this.translationDataByPage.has(pageNum)) continue;
      await Promise.allSettled([
        this.renderPdfPage(docId, pageNum, generation),
        this.loadTranslationPage(docId, pageNum, generation),
      ]);
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    }
  }

  async rerenderPdfDocument(docId, total) {
    const generation = ++this.readerRenderGeneration;
    for (const task of this.pdfRenderTasks.values()) { try { task?.cancel(); } catch (_) {} }
    this.pdfRenderTasks.clear(); this.pdfPageStates.clear();
    this.pdfDocumentEl?.querySelectorAll('.paper-pdf-page-content').forEach((content) => {
      content.empty(); content.createDiv('paper-loading').setText('확대 비율을 적용하는 중…');
    });
    await this.renderPdfPage(docId, this.readerPage, generation);
    this.scrollReaderPaneToPage(this.pdfViewport, '.paper-pdf-page-slot', this.readerPage, false);
    void this.renderRemainingReaderPages(docId, total, generation);
  }

  bindContinuousPageTracking(container, selector, docId, total) {
    container.addEventListener('scroll', () => {
      if (this.readerScrollTimer) window.clearTimeout(this.readerScrollTimer);
      this.readerScrollTimer = window.setTimeout(() => {
        const pages = [...container.querySelectorAll(selector)]; if (!pages.length) return;
        const rect = container.getBoundingClientRect(); const center = rect.top + rect.height * .42;
        const visible = pages.reduce((best, item) => {
          const distance = Math.abs((item.getBoundingClientRect().top + Math.min(item.getBoundingClientRect().height, rect.height) * .35) - center);
          return !best || distance < best.distance ? { item, distance } : best;
        }, null);
        const page = Number(visible?.item?.dataset.page || 0); if (!page || page === this.readerPage) return;
        this.readerPage = page;
        const input = this.bodyEl.querySelector('.paper-page-controls input'); if (input) input.value = String(page);
        void this.saveProgress(docId, page, total);
      }, 140);
    }, { passive: true });
  }

  scrollReaderPaneToPage(container, selector, page, smooth = true) {
    const target = container?.querySelector(`${selector}[data-page="${page}"]`); if (!target) return;
    const top = target.offsetTop - 8;
    container.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' });
  }

  async loadPdfOutline() {
    if (!this.pdfDocument) return;
    const outline = await this.pdfDocument.getOutline();
    const flattened = [];
    const walk = async (items, depth = 0) => {
      for (const item of items || []) {
        let page = null;
        try {
          let destination = item.dest;
          if (typeof destination === 'string') destination = await this.pdfDocument.getDestination(destination);
          if (Array.isArray(destination) && destination[0]) page = (await this.pdfDocument.getPageIndex(destination[0])) + 1;
        } catch (_) {}
        const title = item.title?.trim().replace(/^[\s.·–—-]+/, '');
        if (title && page) flattened.push({ title, page, depth });
        await walk(item.items, depth + 1);
      }
    };
    await walk(outline || []);
    this.pdfOutline = flattened;
  }

  async renderPdfPage(docId, pageNum, generation = this.readerRenderGeneration) {
    if (!this.pdfDocument || !this.pdfViewport || generation !== this.readerRenderGeneration) return;
    if (this.pdfPageStates.has(pageNum)) return this.pdfPageStates.get(pageNum);
    const content = this.pdfDocumentEl?.querySelector(`.paper-pdf-page-slot[data-page="${pageNum}"] .paper-pdf-page-content`);
    if (!content) return;
    try { this.pdfRenderTasks.get(pageNum)?.cancel(); } catch (_) {}
    content.empty(); content.createDiv('paper-loading').setText(`${pageNum}쪽 원문을 렌더링하는 중…`);
    const page = await this.pdfDocument.getPage(pageNum);
    if (generation !== this.readerRenderGeneration) return;
    const baseViewport = page.getViewport({ scale: 1 });
    const available = Math.max(320, this.pdfViewport.clientWidth - 70);
    const selected = this.pdfScaleSelect?.value || 'fit';
    const scale = selected === 'fit' ? Math.min(2.2, available / baseViewport.width) : Number(selected);
    const viewport = page.getViewport({ scale });
    const dpr = window.devicePixelRatio || 1;
    content.empty();
    const wrapper = content.createDiv('paper-native-pdf-page');
    wrapper.style.width = `${viewport.width}px`; wrapper.style.height = `${viewport.height}px`;
    const canvas = wrapper.createEl('canvas');
    canvas.width = Math.floor(viewport.width * dpr); canvas.height = Math.floor(viewport.height * dpr);
    canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`;
    const context = canvas.getContext('2d');
    const renderTask = page.render({ canvasContext: context, viewport, transform: dpr === 1 ? null : [dpr, 0, 0, dpr, 0, 0] });
    this.pdfRenderTasks.set(pageNum, renderTask); this.pdfRenderTask = renderTask;
    await renderTask.promise;
    if (generation !== this.readerRenderGeneration) return;

    const textLayer = wrapper.createDiv('paper-pdf-text-layer textLayer');
    textLayer.style.width = `${viewport.width}px`; textLayer.style.height = `${viewport.height}px`;
    textLayer.style.setProperty('--scale-factor', String(viewport.scale));
    const textContent = await page.getTextContent();
    const textDivs = [];
    const textTask = getPdfJs().renderTextLayer({ textContent, container: textLayer, viewport, textDivs });
    await textTask.promise;
    const state = { pageNum, page, viewport, canvas, wrapper, textLayer, textContent, textDivs, generation, iconPositions: [] };
    this.pdfPageStates.set(pageNum, state); this.pdfPageState = state;
    this.applyPdfHighlights(docId, pageNum, state);
    this.applySentenceMapping(state, this.translationDataByPage.get(pageNum));
    this.renderSectionExplanationButtons(docId, state);
    this.renderFigureExplanationButtons(docId, state);
    this.renderCitationLinks(docId, state);
    textLayer.addEventListener('mouseup', (event) => this.handlePdfSelection(event, docId, state));
    return state;
  }

  applySentenceMapping(state, translationData) {
    const sentences = translationData?.sentences || [];
    if (!state || !sentences.length) return;
    const itemRanges = []; let normalized = '';
    (state.textContent.items || []).forEach((item, index) => {
      const value = normalizedPaperText(item.str); const start = normalized.length; normalized += value;
      itemRanges.push({ index, start, end: normalized.length });
    });
    let cursor = 0;
    sentences.forEach((sentence, sentenceIndex) => {
      const source = normalizedPaperText(sentence.src || sentence.source || '');
      if (!source) return;
      let start = normalized.indexOf(source, cursor);
      if (start < 0) start = normalized.indexOf(source.slice(0, Math.min(source.length, 28)), Math.max(0, cursor - 100));
      if (start < 0) return;
      const end = start + source.length; cursor = Math.max(cursor, end);
      itemRanges.filter((range) => range.end > start && range.start < end).forEach((range) => {
        const span = state.textDivs[range.index];
        if (!span || span.dataset.sentenceIdx) return;
        span.dataset.sentenceIdx = String(sentenceIndex); span.dataset.page = String(state.pageNum); span.classList.add('paper-source-sentence');
        span.onclick = (event) => { event.stopPropagation(); void this.scrollTranslationToSentence(state.pageNum, sentenceIndex); };
      });
    });
  }

  highlightSentence(elements, className) {
    this.bodyEl.querySelectorAll(`.${className}`).forEach((element) => element.removeClass(className));
    elements.forEach((element) => element.addClass(className));
    window.setTimeout(() => elements.forEach((element) => element.removeClass(className)), 2400);
  }

  scrollElementInContainer(container, target) {
    if (!container || !target) return;
    const containerRect = container.getBoundingClientRect(); const targetRect = target.getBoundingClientRect();
    const top = container.scrollTop + targetRect.top - containerRect.top - container.clientHeight / 2 + targetRect.height / 2;
    container.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  async scrollTranslationToSentence(page, index) {
    if (this.activeReaderSideTab !== 'translation') await this.selectReaderSideTab('translation', this.currentReaderDoc.id);
    await this.loadTranslationPage(this.currentReaderDoc.id, page, this.readerRenderGeneration);
    const target = this.sideContent?.querySelector(`.paper-trans-sentence[data-page="${page}"][data-sentence-idx="${index}"]`);
    if (!target) return;
    this.scrollElementInContainer(this.sideContent, target);
    this.highlightSentence([target], 'is-cross-highlight');
  }

  async scrollSourceToSentence(page, index) {
    await this.renderPdfPage(this.currentReaderDoc.id, page, this.readerRenderGeneration);
    const targets = [...(this.pdfPageStates.get(page)?.textLayer?.querySelectorAll(`[data-sentence-idx="${index}"]`) || [])];
    if (!targets.length) return;
    this.scrollElementInContainer(this.pdfViewport, targets[0]);
    this.highlightSentence(targets, 'is-cross-highlight');
  }

  mountTranslationDocument(docId, total) {
    this.sideContent.empty(); this.translationPageEls.clear();
    const documentEl = this.sideContent.createDiv('paper-continuous-translation-document');
    for (let page = 1; page <= total; page += 1) {
      const slot = documentEl.createDiv('paper-translation-page'); slot.dataset.page = String(page);
      slot.createDiv('paper-translation-page-label').setText(`${page} / ${total}`);
      const content = slot.createDiv('paper-translation-page-content');
      this.translationPageEls.set(page, content);
      const cached = this.translationDataByPage.get(page);
      if (cached) void this.renderTranslationPage(page, cached);
      else content.createDiv('paper-loading').setText(`${page}쪽 번역 준비 중…`);
    }
    this.bindContinuousPageTracking(this.sideContent, '.paper-translation-page', docId, total);
  }

  async loadTranslationPage(docId, page, generation = this.readerRenderGeneration) {
    if (this.translationDataByPage.has(page)) {
      const content = this.translationPageEls.get(page);
      if (!content?.querySelector('.paper-translation')) await this.renderTranslationPage(page, this.translationDataByPage.get(page));
      return this.translationDataByPage.get(page);
    }
    try {
      const data = (await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/translation/${page}`)).json;
      if (generation !== this.readerRenderGeneration) return null;
      this.translationDataByPage.set(page, data);
      await this.renderTranslationPage(page, data);
      const state = this.pdfPageStates.get(page); if (state) this.applySentenceMapping(state, data);
      return data;
    } catch (error) {
      const content = this.translationPageEls.get(page);
      if (content?.isConnected) { content.empty(); content.createDiv('paper-empty').setText(error.message.includes('404') ? '이 페이지는 아직 번역되지 않았습니다.' : error.message); }
      return null;
    }
  }

  async renderTranslationPage(page, data) {
    const content = this.translationPageEls.get(page); if (!content?.isConnected || !data) return;
    content.empty();
    const article = content.createDiv('paper-translation markdown-rendered');
    const sentences = data.sentences || [];
    if (sentences.length) {
      for (let index = 0; index < sentences.length; index += 1) {
        const sentence = sentences[index];
        const block = article.createDiv('paper-trans-sentence'); block.dataset.sentenceIdx = String(index); block.dataset.page = String(page);
        const markdown = translationMarkdown(sentence.trans || sentence.translated || sentence.translation || '');
        await MarkdownRenderer.render(this.app, markdown || ' ', block, '', this);
        block.onclick = () => void this.scrollSourceToSentence(page, index);
      }
    } else {
      await MarkdownRenderer.render(this.app, translationMarkdown(data.translation) || '번역된 내용이 없습니다.', article, '', this);
    }
  }

  async selectReaderSideTab(tab, docId) {
    this.activeReaderSideTab = tab;
    Object.entries(this.sideTabButtons || {}).forEach(([key, button]) => button.toggleClass('is-active', key === tab));
    if (tab === 'translation') {
      this.mountTranslationDocument(docId, this.pdfDocument.numPages);
      await this.loadTranslationPage(docId, this.readerPage, this.readerRenderGeneration);
      this.scrollReaderPaneToPage(this.sideContent, '.paper-translation-page', this.readerPage, false);
      void this.renderRemainingReaderPages(docId, this.pdfDocument.numPages, this.readerRenderGeneration);
    }
    else if (tab === 'outline') this.renderOutline(docId);
    else if (tab === 'references') this.renderReferences(docId);
    else this.renderChat(docId);
  }

  renderOutline(docId) {
    this.sideContent.empty();
    const list = this.sideContent.createDiv('paper-outline-list');
    const items = this.pdfOutline.length ? this.pdfOutline : Array.from({ length: this.pdfDocument?.numPages || 0 }, (_, index) => ({ title: `${index + 1} 페이지`, page: index + 1, depth: 0 }));
    items.forEach((item, index) => {
      const row = list.createDiv('paper-outline-row'); row.style.paddingLeft = `${8 + item.depth * 14}px`;
      const link = row.createEl('button', { text: item.title, cls: 'paper-outline-link' });
      link.onclick = () => void this.setReaderPage(docId, item.page, this.pdfDocument.numPages);
      const explain = row.createEl('button', { text: '✦', cls: 'paper-inline-explain', attr: { title: `${item.title} 설명`, 'aria-label': `${item.title} 설명` } });
      explain.onclick = async () => {
        const context = this.pdfOutline.length ? await this.sectionContext(index) : await this.pageContext(item.page, item.title);
        new ExplanationPopup(this, { docId, kind: 'section', label: item.title, page: item.page, context, anchor: explain }).open();
      };
    });
  }

  async sectionContext(index) {
    const item = this.pdfOutline[index] || { title: `${this.readerPage} 페이지`, page: this.readerPage };
    const next = this.pdfOutline[index + 1]; const endPage = Math.min(this.pdfDocument.numPages, next ? next.page : item.page + 1);
    const chunks = [];
    for (let pageNum = item.page; pageNum <= endPage && chunks.join('').length < 16000; pageNum += 1) {
      const page = await this.pdfDocument.getPage(pageNum); const content = await page.getTextContent();
      chunks.push(`[Page ${pageNum}]\n${(content.items || []).map((entry) => entry.str).join(' ')}`);
    }
    return `[섹션 제목]\n${item.title}\n\n${chunks.join('\n\n').slice(0, 18000)}`;
  }

  async pageContext(pageNum, title = '') {
    const page = await this.pdfDocument.getPage(pageNum); const content = await page.getTextContent();
    return `[섹션 제목]\n${title}\n\n[Page ${pageNum} 원문]\n${(content.items || []).map((entry) => entry.str).join(' ').slice(0, 18000)}`;
  }

  explanationIconPosition(state, span, fallbackLeft = 8, fallbackTop = 8) {
    const size = 19; const gap = 4; const wrapperRect = state.wrapper.getBoundingClientRect();
    const textRects = state.textDivs.map((item) => {
      const rect = item.getBoundingClientRect();
      return { left: rect.left - wrapperRect.left, top: rect.top - wrapperRect.top, right: rect.right - wrapperRect.left, bottom: rect.bottom - wrapperRect.top };
    }).filter((rect) => rect.right > rect.left && rect.bottom > rect.top);
    const spanRect = span ? (() => {
      const rect = span.getBoundingClientRect();
      return { left: rect.left - wrapperRect.left, top: rect.top - wrapperRect.top, right: rect.right - wrapperRect.left, bottom: rect.bottom - wrapperRect.top };
    })() : { left: fallbackLeft + size + gap, top: fallbackTop, right: fallbackLeft + size * 2, bottom: fallbackTop + size };
    const clamp = (value, max) => Math.max(3, Math.min(max - size - 3, value));
    const candidates = [
      { left: spanRect.left - size - gap, top: spanRect.top },
      { left: spanRect.left, top: spanRect.top - size - gap },
      { left: spanRect.right + gap, top: spanRect.top },
      { left: spanRect.left, top: spanRect.bottom + gap },
      { left: 5, top: spanRect.top },
      { left: state.viewport.width - size - 5, top: spanRect.top },
      { left: fallbackLeft, top: fallbackTop },
    ];
    for (let delta = 22; delta <= 132; delta += 22) {
      candidates.push({ left: 5, top: spanRect.top - delta }, { left: 5, top: spanRect.top + delta });
    }
    const available = (candidate) => {
      const left = clamp(candidate.left, state.viewport.width); const top = clamp(candidate.top, state.viewport.height);
      const right = left + size; const bottom = top + size;
      const textOverlap = textRects.some((rect) => right + 2 > rect.left && left - 2 < rect.right && bottom + 2 > rect.top && top - 2 < rect.bottom);
      const iconOverlap = (state.iconPositions || []).some((item) => right + 2 > item.left && left - 2 < item.left + size && bottom + 2 > item.top && top - 2 < item.top + size);
      return textOverlap || iconOverlap ? null : { left, top };
    };
    let position = null;
    for (const candidate of candidates) { position = available(candidate); if (position) break; }
    if (!position) position = { left: -23, top: clamp(spanRect.top, state.viewport.height) };
    state.iconPositions.push(position); return position;
  }

  renderSectionExplanationButtons(docId, state = this.pdfPageState) {
    if (!state) return;
    let sections = this.pdfOutline.filter((item) => item.page === state.pageNum);
    const entries = (state.textContent.items || []).map((item, sourceIndex) => ({
      title: String(item.str || '').trim(), sourceIndex,
      size: Math.abs(Number(item.transform?.[3] || 0)),
    })).filter((item) => item.title);
    const sizes = entries.map((item) => item.size).filter(Boolean).sort((a, b) => a - b);
    const median = sizes[Math.floor(sizes.length / 2)] || 10;
    const canonicalHeading = /^(?:abstract|introduction|background|related work|preliminar(?:y|ies)|method(?:s|ology)?|approach|framework|implementation(?: details)?|experiment(?:s)?|evaluation(?: protocol)?|main results|qualitative results|discussion|analysis|ablation(?: study)?|limitations?(?: and future work)?|conclusion(?:s)?|future work|references|appendix)[:.]?$/i;
    const numberedHeading = /^(?:\d+(?:\.\d+)*|[IVXLCDM]+|[A-Z])[.)]\s+[A-Za-z][A-Za-z0-9 :&/()'’\-]{3,100}$/;
    const detected = entries.filter((item) => {
      if (item.title.length >= 120 || item.title.split(/\s+/).length > 14) return false;
      if (numberedHeading.test(item.title)) return item.size >= median * 1.08;
      return canonicalHeading.test(item.title) && item.size >= median * 1.16;
    }).map((item) => ({ ...item, page: state.pageNum, detected: true }));
    const knownTitles = new Set(sections.map((item) => normalizedPaperText(item.title)));
    sections = sections.concat(detected.filter((item) => ![...knownTitles].some((known) => known.includes(normalizedPaperText(item.title)) || normalizedPaperText(item.title).includes(known))));
    sections.forEach((section) => {
      const needle = normalizedPaperText(section.title); if (!needle) return;
      const matchIndex = Number.isFinite(section.sourceIndex) ? section.sourceIndex : (state.textContent.items || []).findIndex((item) => needle.includes(normalizedPaperText(item.str)) && normalizedPaperText(item.str).length >= 3);
      const span = state.textDivs[matchIndex]; if (!span) return;
      const button = state.wrapper.createEl('button', { text: '✦', cls: 'paper-page-explain-button', attr: { title: `${section.title} 설명`, 'aria-label': `${section.title} 설명` } });
      const position = this.explanationIconPosition(state, span);
      button.style.left = `${position.left}px`; button.style.top = `${position.top}px`;
      button.onclick = async () => {
        const index = this.pdfOutline.indexOf(section);
        const context = section.detected ? await this.pageContext(section.page, section.title) : await this.sectionContext(index);
        new ExplanationPopup(this, { docId, kind: 'section', label: section.title, page: section.page, context, anchor: button }).open();
      };
    });
  }

  renderFigureExplanationButtons(docId, state = this.pdfPageState) {
    if (!state) return;
    this.documentImages.filter((item) => item.page === state.pageNum && /^(?:fig(?:ure)?|table)\b/i.test(item.label || '')).forEach((item) => {
      const button = state.wrapper.createEl('button', { text: '✦', cls: 'paper-page-explain-button is-visual', attr: { title: `${item.label} 설명`, 'aria-label': `${item.label} 설명` } });
      const needle = normalizedPaperText(item.label);
      const labelId = needle.replace(/^(?:figure|fig|table)/, '');
      const labelIndex = (state.textContent.items || []).findIndex((entry) => {
        const text = normalizedPaperText(entry.str);
        if (labelId && !text.includes(labelId)) return false;
        return text.length >= 4 && (needle.includes(text) || text.includes(needle));
      });
      const labelSpan = state.textDivs[labelIndex];
      const position = this.explanationIconPosition(
        state, labelSpan,
        Math.max(3, item.left / 100 * state.viewport.width - 23),
        Math.max(3, item.top / 100 * state.viewport.height),
      );
      button.style.left = `${position.left}px`; button.style.top = `${position.top}px`;
      button.onclick = () => {
        const imageDataUrl = this.cropVisual(item, state);
        const kind = /^table/i.test(item.label || '') ? 'table' : 'figure';
        new ExplanationPopup(this, { docId, kind, label: item.label, page: item.page, context: item.caption || '', imageDataUrl, anchor: button }).open();
      };
    });
  }

  cropVisual(item, state = this.pdfPageState) {
    if (!state) return null;
    const crop = document.createElement('canvas');
    const x = item.left / 100 * state.canvas.width; const y = item.top / 100 * state.canvas.height;
    const width = Math.max(1, item.width / 100 * state.canvas.width); const height = Math.max(1, item.height / 100 * state.canvas.height);
    crop.width = width; crop.height = height;
    crop.getContext('2d').drawImage(state.canvas, x, y, width, height, 0, 0, width, height);
    return crop.toDataURL('image/png');
  }

  renderCitationLinks(docId, state = this.pdfPageState) {
    if (!state) return;
    const bind = (span, numbers) => {
      const known = numbers.filter((number) => this.referenceData?.references?.[number]);
      if (!known.length) return;
      const merged = new Set(String(span.dataset.referenceNumbers || '').split(',').filter(Boolean).concat(known));
      span.dataset.referenceNumbers = [...merged].join(',');
      span.classList.add('paper-citation-link');
      span.title = `인용논문 ${[...merged].map((number) => `[${number}]`).join(', ')}`;
      if (span.dataset.referenceBound) return;
      span.dataset.referenceBound = 'true';
      span.addEventListener('click', (event) => {
        event.stopPropagation();
        const number = String(span.dataset.referenceNumbers || '').split(',')[0];
        void this.selectReaderSideTab('references', docId).then(() => {
          const target = this.sideContent.querySelector(`[data-reference-number="${number}"]`);
          target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          if (target) this.highlightSentence([target], 'is-cross-highlight');
        });
      });
    };
    state.textDivs.forEach((span) => {
      const numbers = parseReferenceNumbers(span.textContent);
      if (numbers.length) bind(span, numbers);
    });

    const ranges = []; let fullText = '';
    (state.textContent.items || []).forEach((item, index) => {
      const value = normalizedPaperText(item.str); const start = fullText.length; fullText += value;
      ranges.push({ index, start, end: fullText.length });
    });
    Object.entries(this.referenceData?.mentions || {}).forEach(([number, mention]) => {
      for (const value of [...(mention.titles || []), ...(mention.authors || [])]) {
        const needle = normalizedPaperText(value);
        if (needle.length < 10) continue;
        let offset = fullText.indexOf(needle);
        while (offset >= 0) {
          const end = offset + needle.length;
          ranges.filter((range) => range.end > offset && range.start < end).forEach((range) => bind(state.textDivs[range.index], [number]));
          offset = fullText.indexOf(needle, end);
        }
      }
    });
  }

  async referenceContext(number) {
    if (this.citationContextCache.has(number)) return this.citationContextCache.get(number);
    const excerpts = [];
    const mention = this.referenceData?.mentions?.[number] || {};
    const titleNeedles = [...(mention.titles || []), ...(mention.authors || [])]
      .map((value) => String(value || '').trim().toLocaleLowerCase()).filter((value) => value.length >= 10);
    for (let pageNum = 1; pageNum <= (this.pdfDocument?.numPages || 0) && excerpts.length < 3; pageNum += 1) {
      const page = await this.pdfDocument.getPage(pageNum); const content = await page.getTextContent();
      const pageText = (content.items || []).map((entry) => entry.str).join(' ').replace(/\s+/g, ' ').trim();
      const matches = [];
      for (const match of pageText.matchAll(/\[[^\]]{1,60}\]/g)) {
        if (parseReferenceNumbers(match[0]).includes(String(number))) matches.push(match.index || 0);
      }
      if (!matches.length) {
        const lower = pageText.toLocaleLowerCase();
        for (const needle of titleNeedles) {
          const index = lower.indexOf(needle);
          if (index >= 0) { matches.push(index); break; }
        }
      }
      for (const index of matches.slice(0, 2)) {
        excerpts.push(`[Page ${pageNum}] ${pageText.slice(Math.max(0, index - 700), Math.min(pageText.length, index + 700))}`);
        if (excerpts.length >= 3) break;
      }
    }
    const value = excerpts.join('\n\n'); this.citationContextCache.set(number, value); return value;
  }

  renderReferences(docId) {
    this.sideContent.empty();
    const references = this.referenceData?.references || {};
    const numbers = Object.keys(references).sort((a, b) => Number(a) - Number(b));
    const header = this.sideContent.createDiv('paper-reference-header');
    header.createEl('h3', { text: `인용논문 ${numbers.length}개` });
    header.createEl('p', { text: '본문의 [번호]를 누르면 해당 항목으로 이동합니다.' });
    const list = this.sideContent.createDiv('paper-reference-list');
    numbers.forEach((number) => {
      const item = list.createDiv('paper-reference-item'); item.dataset.referenceNumber = number;
      item.createEl('strong', { text: `[${number}]` }); item.createEl('p', { text: references[number] });
      const result = item.createDiv('paper-reference-result');
      const actions = item.createDiv('paper-card-actions');
      const find = actions.createEl('button', { text: '논문 찾기' });
      const insight = actions.createEl('button', { text: '인용 이유·개요' });
      const download = actions.createEl('button', { text: 'PDF 다운로드' });
      find.onclick = async () => {
        result.empty(); result.createDiv('paper-loading').setText('여러 학술 소스에서 찾는 중…');
        try {
          const resolved = (await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/references/${encodeURIComponent(number)}`)).json;
          item.resolvedReference = resolved; result.empty();
          result.createEl('strong', { text: resolved.title || references[number] });
          result.createEl('p', { text: [plainAuthors(resolved.authors), resolved.year, resolved.venue].filter(Boolean).join(' · ') });
          if (resolved.url) result.createEl('a', { text: '학술 페이지 열기', href: resolved.url, attr: { target: '_blank', rel: 'noopener' } });
        } catch (error) { result.empty(); result.createDiv('paper-error').setText(error.message); }
      };
      insight.onclick = async () => {
        result.empty(); result.createDiv('paper-loading').setText('인용 관계를 분석하는 중…');
        try {
          const surroundingContext = await this.referenceContext(number);
          const response = await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/references/${encodeURIComponent(number)}/insight`, { method: 'POST', json: { surrounding_context: surroundingContext || references[number] } });
          result.empty(); await MarkdownRenderer.render(this.app, response.json.content || '', result, '', this);
        } catch (error) { result.empty(); result.createDiv('paper-error').setText(error.message); }
      };
      download.onclick = () => void this.downloadReference(docId, number, item.resolvedReference, references[number]);
    });
    if (!numbers.length) list.createDiv('paper-empty').setText('PDF에서 참고문헌 목록을 찾지 못했습니다.');
  }

  async downloadReference(docId, number, resolved, fallbackTitle) {
    const notice = new Notice(`[${number}] PDF를 내려받는 중…`, 0);
    try {
      if (!resolved) resolved = (await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/references/${encodeURIComponent(number)}`)).json;
      const response = await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/references/${encodeURIComponent(number)}/download`);
      const folder = normalizePath(`${this.plugin.settings.pdfFolder}/Citations`);
      await this.plugin.ensureVaultFolder(folder);
      let target = normalizePath(`${folder}/${safeName(resolved.title || fallbackTitle, `reference-${number}`)}.pdf`);
      if (this.app.vault.getAbstractFileByPath(target)) target = normalizePath(`${folder}/${safeName(resolved.title || fallbackTitle, `reference-${number}`)}-${Date.now()}.pdf`);
      await this.app.vault.createBinary(target, response.arrayBuffer);
      notice.hide(); new Notice('인용논문 PDF를 Vault에 저장했습니다.');
      await this.app.workspace.openLinkText(target, '', false);
    } catch (error) { notice.hide(); new Notice(`PDF 다운로드 실패: ${error.message}`); }
  }

  handlePdfSelection(event, docId, state = this.pdfPageState) {
    window.setTimeout(() => {
      const selection = window.getSelection(); const term = selection?.toString().trim().replace(/\s+/g, ' ');
      if (!term || term.length > 160 || !/[A-Za-z]/.test(term)) { this.removeSelectionToolbar(); return; }
      const range = selection.getRangeAt(0).cloneRange(); const rect = range.getBoundingClientRect();
      if (!rect.width && !rect.height) return;
      this.removeSelectionToolbar();
      const toolbar = document.createElement('div'); toolbar.className = 'paper-selection-toolbar';
      toolbar.style.left = `${Math.max(8, Math.min(window.innerWidth - 270, rect.left))}px`; toolbar.style.top = `${Math.max(8, rect.top - 42)}px`;
      const copy = document.createElement('button'); copy.textContent = '복사';
      const highlight = document.createElement('button'); highlight.textContent = '하이라이트';
      const vocab = document.createElement('button'); vocab.textContent = '단어장';
      toolbar.append(copy, highlight, vocab); document.body.appendChild(toolbar); this.selectionToolbar = toolbar;
      copy.onclick = async () => { await navigator.clipboard.writeText(term); new Notice('복사했습니다.'); this.removeSelectionToolbar(); };
      const selectedIndices = (state?.textDivs || []).map((span, index) => {
        try { return range.intersectsNode(span) ? index : -1; } catch (_) { return -1; }
      }).filter((index) => index >= 0);
      highlight.onclick = async () => {
        if (!selectedIndices.length) return;
        selectedIndices.forEach((index) => state?.textDivs?.[index]?.addClass('paper-user-highlight'));
        await this.plugin.savePdfHighlight(docId, state.pageNum, selectedIndices, term);
        new Notice('하이라이트를 저장했습니다.'); this.removeSelectionToolbar();
      };
      const sourceElement = event.target.closest?.('[data-sentence-idx]');
      const sentenceIndex = Number(sourceElement?.dataset.sentenceIdx);
      const sentence = Number.isFinite(sentenceIndex) ? this.translationDataByPage.get(state.pageNum)?.sentences?.[sentenceIndex] : null;
      vocab.onclick = () => {
        this.removeSelectionToolbar();
        void this.plugin.openVocabularyCard({
          doc_id: docId, term, page_num: state.pageNum,
          context_en: sentence?.src || term, context_ko: sentence?.trans || '',
          paper_title: paperTitle(this.currentReaderDoc), sync_anki: true,
        });
      };
    }, 0);
  }

  applyPdfHighlights(docId, pageNum, state = this.pdfPageState) {
    const entries = this.plugin.settings.pdfHighlights?.[docId]?.[String(pageNum)] || [];
    entries.forEach((entry) => (entry.indices || []).forEach((index) => state?.textDivs?.[index]?.addClass('paper-user-highlight')));
  }

  removeSelectionToolbar() { this.selectionToolbar?.remove(); this.selectionToolbar = null; }

  renderChat(docId) {
    this.sideContent.empty();
    const messages = this.sideContent.createDiv('paper-chat-messages');
    this.chatMessages.forEach((message) => {
      const bubble = messages.createDiv(`paper-chat-message is-${message.role}`); bubble.setText(message.content);
    });
    const compose = this.sideContent.createDiv('paper-chat-compose');
    const input = compose.createEl('textarea', { placeholder: '이 논문에 관해 질문하세요' });
    const send = compose.createEl('button', { text: '질문', cls: 'mod-cta' });
    send.onclick = async () => {
      const question = input.value.trim(); if (!question) return;
      this.chatMessages.push({ role: 'user', content: question }); input.value = ''; send.disabled = true; this.renderChat(docId);
      try {
        const response = await this.plugin.api('/api/chat/stream', { method: 'POST', json: { session_id: docId, messages: this.chatMessages } });
        this.chatMessages.push({ role: 'assistant', content: response.text.trim() });
      } catch (error) { this.chatMessages.push({ role: 'assistant', content: `오류: ${error.message}` }); }
      this.renderChat(docId);
    };
  }

  async saveProgress(docId, page, total) {
    try {
      const today = new Date();
      const activityDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      await this.plugin.api(`/api/library/${encodeURIComponent(docId)}/reading-progress`, { method: 'POST', json: { page, total_pages: total, activity_date: activityDate, remember_position: true } });
    } catch (error) { console.warn('Failed to save reading progress', error); }
  }

  async onClose() { this.releasePdf(); }
}

class ResearchWorkspaceSettingTab extends PluginSettingTab {
  constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: '논문 연구 통합 설정' });
    const fields = [
      ['연결 파일', 'connectionFile', '이미 실행 중인 로컬 엔진을 재사용할 때 읽는 연결 파일입니다.'],
      ['연구 엔진 실행 파일', 'backendExecutable', '플러그인이 직접 실행할 로컬 백엔드 파일입니다.'],
      ['데이터 디렉터리', 'dataDirectory', '비워두면 기존 논문 데이터가 있는 기본 위치를 사용합니다.'],
      ['노트 폴더', 'noteFolder', '생성된 Markdown 논문 노트를 저장합니다.'],
      ['PDF 폴더', 'pdfFolder', '보관함 PDF를 Vault로 복사할 위치입니다.'],
      ['이미지 폴더', 'assetFolder', 'Figure와 Table 이미지를 저장합니다.'],
    ];
    fields.forEach(([name, key, desc]) => {
      new Setting(containerEl).setName(name).setDesc(desc).addText((text) => text
        .setValue(this.plugin.settings[key] || '')
        .onChange(async (value) => {
          this.plugin.settings[key] = value.trim();
          this.plugin.connection = null;
          await this.plugin.saveSettings();
        }));
    });
  }
}

module.exports = class PaperResearchWorkspacePlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    if (shouldMigrateBackendExecutable(this.settings.backendExecutable)) {
      this.settings.backendExecutable = defaultBackendExecutable();
      await this.saveSettings();
    }
    pdfJsBaseDir = path.join(this.app.vault.adapter.basePath, this.manifest.dir || `.obsidian/plugins/${this.manifest.id}`);
    pdfJsWorkerSrc = this.app.vault.adapter.getResourcePath(normalizePath(`${this.manifest.dir || `.obsidian/plugins/${this.manifest.id}`}/pdfjs/pdf.worker.js`));
    this.connection = null;
    this.connectionPromise = null;
    this.backendChild = null;
    this.backendStartPromise = null;
    this.currentDocId = null;
    addIcon(PAPER_RESEARCH_ICON, PAPER_RESEARCH_ICON_SVG);
    this.registerView(VIEW_TYPE, (leaf) => new ResearchWorkspaceView(leaf, this));
    // 이전 리본 항목은 Obsidian 사용자 지정에 숨김(false)으로 저장되어 있었다.
    // 새 이름으로 등록해 전용 아이콘이 기본 표시되게 한다.
    const ribbonButton = this.addRibbonIcon(PAPER_RESEARCH_ICON, 'AI 논문 연구 열기', () => void this.activateView());
    ribbonButton.addClass('paper-research-ribbon-icon');
    const revealRibbonButton = () => ribbonButton.style.setProperty('display', 'flex', 'important');
    revealRibbonButton();
    this.app.workspace.onLayoutReady(revealRibbonButton);
    const ribbonObserver = new MutationObserver(revealRibbonButton);
    ribbonObserver.observe(ribbonButton, { attributes: true, attributeFilter: ['style'] });
    this.register(() => ribbonObserver.disconnect());
    this.addCommand({ id: 'open-workspace', name: '논문 연구 작업공간 열기', callback: () => void this.activateView() });
    this.addCommand({ id: 'import-active-pdf', name: '현재 PDF를 보관함으로 가져오기', checkCallback: (checking) => {
      const file = this.app.workspace.getActiveFile();
      if (!file || file.extension.toLowerCase() !== 'pdf') return false;
      if (!checking) void this.importPdf(file);
      return true;
    }});
    this.addCommand({ id: 'sync-paper-notes', name: '생성된 논문 노트를 Vault와 동기화', callback: () => void this.syncAllNotes() });
    this.addCommand({ id: 'export-current-note', name: '현재 논문 노트를 Vault로 내보내기', callback: () => void this.exportCurrentNote() });
    this.addCommand({ id: 'open-scholar', name: 'Scholar 열기', callback: () => void this.openRoute('#scholar') });
    this.addCommand({ id: 'open-research', name: '연구 탐색 열기', callback: () => void this.openRoute('#research') });
    this.addCommand({ id: 'open-history', name: '논문 읽기 히스토리 열기', callback: () => void this.openRoute('#history') });
    this.addCommand({ id: 'open-notes', name: '보관함에서 논문 노트 열기', callback: () => void this.openRoute('#library') });
    this.addCommand({ id: 'open-chats', name: '논문 채팅 기록 열기', callback: () => void this.openRoute('#chats') });
    this.addCommand({ id: 'review-vocabulary', name: '논문 단어장과 복습 열기', callback: () => void this.openRoute('#vocabulary') });
    this.addCommand({ id: 'add-selection-to-vocabulary', name: '선택한 영어를 논문 단어장에 추가', editorCheckCallback: (checking, editor, view) => {
      const selection = editor.getSelection().trim();
      const docId = view.file && this.app.metadataCache.getFileCache(view.file)?.frontmatter?.paper_workspace_id;
      if (!selection || !docId) return false;
      if (!checking) void this.addSelectionToVocabulary(editor, view.file, String(docId));
      return true;
    }});
    this.registerEvent(this.app.workspace.on('file-menu', (menu, file) => {
      if (file instanceof TFile && file.extension.toLowerCase() === 'pdf') {
        menu.addItem((item) => item.setTitle('논문 보관함으로 가져오기').setIcon('book-plus').onClick(() => void this.importPdf(file)));
      }
    }));
    this.addSettingTab(new ResearchWorkspaceSettingTab(this.app, this));
  }

  onunload() {
    this.app.workspace.detachLeavesOfType(VIEW_TYPE);
    if (this.backendChild && !this.backendChild.killed) {
      try { this.backendChild.kill('SIGTERM'); } catch (_) {}
      try {
        const connectionPath = this.connectionPath();
        const saved = JSON.parse(fs.readFileSync(connectionPath, 'utf8'));
        if (saved.managedBy === this.manifest.id && Number(saved.pid) === Number(this.backendChild.pid)) fs.unlinkSync(connectionPath);
      } catch (_) {}
    }
    this.backendChild = null;
  }
  async saveSettings() { await this.saveData(this.settings); }

  async activateView() {
    let leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = this.app.workspace.getLeaf('tab');
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    await this.app.workspace.revealLeaf(leaf);
    return leaf.view;
  }

  async openRoute(route) {
    const view = await this.activateView();
    if (view instanceof ResearchWorkspaceView) await view.navigate(route);
  }

  connectionPath() { return this.settings.connectionFile || defaultConnectionFile(); }

  readConnectionFile() {
    const parsed = JSON.parse(fs.readFileSync(this.connectionPath(), 'utf8'));
    if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(parsed.baseUrl || '') || !parsed.token) {
      throw new Error('연결 파일 형식이 올바르지 않습니다.');
    }
    return parsed;
  }

  async probeConnection(connection) {
    const response = await requestUrl({
      url: `${connection.baseUrl}/api/auth/check`,
      method: 'GET',
      headers: { Authorization: `Bearer ${connection.token}` },
      throw: false,
    });
    return response.status === 200;
  }

  dataDirectory() {
    return this.settings.dataDirectory || path.dirname(this.connectionPath());
  }

  async findAvailablePort() {
    return new Promise((resolve, reject) => {
      const server = net.createServer();
      server.unref();
      server.on('error', reject);
      server.listen(0, '127.0.0.1', () => {
        const address = server.address();
        server.close(() => resolve(address.port));
      });
    });
  }

  async startStandaloneBackend() {
    if (this.backendStartPromise) return this.backendStartPromise;
    this.backendStartPromise = (async () => {
      const executable = this.settings.backendExecutable || DEFAULT_SETTINGS.backendExecutable;
      if (!fs.existsSync(executable)) throw new Error(`연구 엔진을 찾을 수 없습니다: ${executable}`);
      const dataDir = this.dataDirectory();
      const directories = {
        uploads: path.join(dataDir, 'uploads'), cache: path.join(dataDir, 'cache'),
        library: path.join(dataDir, 'library'), staging: path.join(dataDir, 'drop-staging'),
        logs: path.join(dataDir, 'logs'),
      };
      [dataDir, ...Object.values(directories)].forEach((dir) => fs.mkdirSync(dir, { recursive: true }));
      const port = await this.findAvailablePort();
      const token = crypto.randomBytes(32).toString('hex');
      const env = Object.assign({}, process.env, {
        APP_HOST: '127.0.0.1', APP_PORT: String(port),
        EASYPAPER_INTEGRATION_TOKEN: token,
        EASYPAPER_CONFIG_DIR: dataDir,
        DB_PATH: path.join(dataDir, 'easypaper.db'),
        UPLOAD_DIR: directories.uploads, CACHE_DIR: directories.cache,
        LIBRARY_DIR: directories.library, DROP_STAGING_DIR: directories.staging,
        EASYPAPER_LOG_DIR: directories.logs,
        EASYPAPER_FRONTEND_DIST: path.join(path.dirname(executable), '_internal', 'frontend', 'dist'),
        PATH: [path.join(os.homedir(), '.local', 'bin'), process.env.PATH || ''].join(path.delimiter),
      });
      const child = spawn(executable, [], { env, cwd: path.dirname(executable), stdio: 'ignore' });
      this.backendChild = child;
      child.once('exit', () => {
        if (this.backendChild === child) {
          this.backendChild = null;
          if (this.connection?.owned) this.connection = null;
        }
      });
      child.once('error', (error) => console.error('Local research engine failed', error));
      const connection = { baseUrl: `http://127.0.0.1:${port}`, token, owned: true, pid: child.pid, managedBy: this.manifest.id };
      try {
        const connectionPath = this.connectionPath();
        fs.mkdirSync(path.dirname(connectionPath), { recursive: true });
        fs.writeFileSync(connectionPath, JSON.stringify(connection, null, 2), { mode: 0o600 });
        fs.chmodSync(connectionPath, 0o600);
      } catch (error) { console.warn('Failed to persist local engine connection', error); }
      return connection;
    })();
    try { return await this.backendStartPromise; }
    finally { this.backendStartPromise = null; }
  }

  async ensureConnection(force = false) {
    if (this.connectionPromise) return this.connectionPromise;
    this.connectionPromise = this.establishConnection(force);
    try { return await this.connectionPromise; }
    finally { this.connectionPromise = null; }
  }

  async establishConnection(force = false) {
    // UI 새로고침(force)은 데이터만 다시 읽는 의미다. 이미 살아 있는 엔진을
    // 버리고 두 번째 프로세스를 띄우면 같은 SQLite 파일을 동시에 쓰게 되므로,
    // 연결은 force와 무관하게 항상 먼저 재사용한다.
    if (this.connection) {
      try { if (await this.probeConnection(this.connection)) return this.connection; } catch (_) {}
    }
    let lastError = null;
    try {
      const discovered = this.readConnectionFile();
      if (await this.probeConnection(discovered)) {
        this.connection = discovered;
        return discovered;
      }
    } catch (error) { lastError = error; }

    const standalone = await this.startStandaloneBackend();
    for (let attempt = 0; attempt < 80; attempt += 1) {
      try {
        if (await this.probeConnection(standalone)) {
          this.connection = standalone;
          return standalone;
        }
      } catch (error) { lastError = error; }
      if (this.backendChild?.exitCode !== null) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw new Error(lastError?.message || '로컬 백엔드가 응답하지 않습니다.');
  }

  async api(endpoint, options = {}) {
    const connection = await this.ensureConnection();
    const headers = Object.assign({}, options.headers, { Authorization: `Bearer ${connection.token}` });
    if (options.json !== undefined) headers['Content-Type'] = 'application/json';
    const response = await requestUrl({
      url: `${connection.baseUrl}${endpoint}`,
      method: options.method || 'GET',
      headers,
      body: options.json !== undefined ? JSON.stringify(options.json) : options.body,
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      let detail = `요청 실패 (${response.status})`;
      try { detail = response.json?.detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response;
  }

  async importActivePdf() {
    const file = this.app.workspace.getActiveFile();
    if (!file || file.extension.toLowerCase() !== 'pdf') {
      new Notice('Obsidian에서 가져올 PDF를 먼저 여세요.');
      return;
    }
    await this.importPdf(file);
  }

  async importPdf(file) {
    const knownId = this.settings.imports[file.path];
    if (knownId) {
      try {
        await this.api(`/api/library/${encodeURIComponent(knownId)}`);
        new Notice('이미 보관함에 있는 논문을 엽니다.');
        await this.openRoute(`#viewer?id=${encodeURIComponent(knownId)}`);
        return;
      } catch (_) { delete this.settings.imports[file.path]; }
    }
    try { await this.uploadPdfData(file.name, await this.app.vault.readBinary(file), file.path, true); }
    catch (error) { new Notice(`PDF를 가져오지 못했습니다: ${error.message}`); }
  }

  async pdfFilesFromDrop(dataTransfer) {
    const files = [];
    const walk = async (entry) => {
      if (!entry) return;
      if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        if (file.name?.toLowerCase().endsWith('.pdf')) files.push(file);
        return;
      }
      if (entry.isDirectory) {
        const reader = entry.createReader();
        while (true) {
          const entries = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
          if (!entries.length) break;
          for (const child of entries) await walk(child);
        }
      }
    };
    const items = [...(dataTransfer?.items || [])];
    for (const item of items) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) await walk(entry);
    }
    if (!files.length) {
      for (const file of [...(dataTransfer?.files || [])]) if (file.name?.toLowerCase().endsWith('.pdf')) files.push(file);
    }
    const seen = new Set();
    return files.filter((file) => {
      const key = `${file.name}:${file.size}:${file.lastModified}`;
      if (seen.has(key)) return false; seen.add(key); return true;
    });
  }

  async importExternalPdf(file, openAfter = true) {
    return this.uploadPdfData(file.name, await file.arrayBuffer(), '', openAfter);
  }

  async uploadPdfData(filename, arrayBuffer, sourcePath = '', openAfter = true) {
    const notice = new Notice(`${filename}을 가져오고 분석을 시작합니다…`, 0);
    try {
      const bytes = Buffer.from(arrayBuffer);
      const boundary = `----PaperResearch${Date.now().toString(16)}`;
      const prefix = Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename.replace(/"/g, '')}"\r\nContent-Type: application/pdf\r\n\r\n`, 'utf8');
      const suffix = Buffer.from(`\r\n--${boundary}--\r\n`, 'utf8');
      const body = Buffer.concat([prefix, bytes, suffix]);
      const response = await this.api('/api/upload?target_lang=%ED%95%9C%EA%B5%AD%EC%96%B4&style=academic&translation_mode=auto', {
        method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
        body: body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength),
      });
      const result = response.json;
      if (sourcePath) { this.settings.imports[sourcePath] = result.session_id; await this.saveSettings(); }
      // Start the translation job explicitly as soon as upload parsing finishes.
      // The backend also supports auto mode, but its note generation can delay the
      // job; this guarantees both single-file and multi-file imports receive a job.
      try {
        await this.api(`/api/jobs/${encodeURIComponent(result.session_id)}/restart`, {
          method: 'POST',
          json: { target_lang: '한국어', style: 'academic', ignore_math: false, ignore_table: true, ignore_refs: false },
        });
      } catch (translationError) {
        new Notice(`${filename}은 가져왔지만 자동 번역 시작에 실패했습니다: ${translationError.message}`);
      }
      notice.hide(); new Notice(`${filename} 가져오기를 완료했습니다. 번역과 노트 생성을 시작합니다.`);
      if (openAfter) await this.openRoute(`#viewer?id=${encodeURIComponent(result.session_id)}`);
      return result;
    } catch (error) { notice.hide(); throw error; }
  }

  async savePdfHighlight(docId, pageNum, indices, text) {
    if (!this.settings.pdfHighlights) this.settings.pdfHighlights = {};
    if (!this.settings.pdfHighlights[docId]) this.settings.pdfHighlights[docId] = {};
    const key = String(pageNum);
    const entries = this.settings.pdfHighlights[docId][key] || [];
    const signature = indices.join(',');
    if (!entries.some((entry) => (entry.indices || []).join(',') === signature)) entries.push({ indices, text, created_at: new Date().toISOString() });
    this.settings.pdfHighlights[docId][key] = entries;
    await this.saveSettings();
  }

  async ensureVaultFolder(folder) {
    const normalized = normalizePath(folder || '');
    if (!normalized) return;
    const parts = normalized.split('/');
    let current = '';
    for (const part of parts) {
      current = current ? `${current}/${part}` : part;
      if (!this.app.vault.getAbstractFileByPath(current)) {
        try { await this.app.vault.createFolder(current); } catch (_) {}
      }
    }
  }

  async getPaperPdfPath(doc, docId) {
    const imported = Object.entries(this.settings.imports).find(([, id]) => id === docId)?.[0];
    if (imported && this.app.vault.getAbstractFileByPath(imported)) return imported;
    const existing = this.settings.pdfPaths[docId];
    if (existing && this.app.vault.getAbstractFileByPath(existing)) {
      // 초기 통합판은 원본 filename에 이미 .pdf가 있는데 확장자를 한 번 더
      // 붙였다. 기존 Vault도 다음 동기화 때 안전하게 정상 이름으로 이관한다.
      if (/\.pdf\.pdf$/i.test(existing)) {
        const corrected = existing.replace(/\.pdf$/i, '');
        const oldFile = this.app.vault.getAbstractFileByPath(existing);
        if (oldFile instanceof TFile && !this.app.vault.getAbstractFileByPath(corrected)) {
          // 이 경로는 곧바로 아래 생성 구간과 frontmatter를 다시 쓰므로 링크
          // 전체 자동 갱신이 필요 없다. fileManager.renameFile은 대형 Vault의
          // 링크 갱신 중 파일 이동 후에도 예외를 낼 수 있어 기본 Vault rename을
          // 사용한다.
          await this.app.vault.rename(oldFile, corrected);
          this.settings.pdfPaths[docId] = corrected;
          await this.saveSettings();
          return corrected;
        }
      }
      return existing;
    }
    await this.ensureVaultFolder(this.settings.pdfFolder);
    const pdfBase = String(doc.filename || doc.metadata?.title || docId).replace(/\.pdf$/i, '');
    const pdfPath = normalizePath(`${this.settings.pdfFolder}/${safeName(pdfBase, docId)}.pdf`);
    if (!this.app.vault.getAbstractFileByPath(pdfPath)) {
      const response = await this.api(`/api/library/${encodeURIComponent(docId)}/pdf`);
      await this.app.vault.createBinary(pdfPath, response.arrayBuffer);
    }
    this.settings.pdfPaths[docId] = pdfPath;
    await this.saveSettings();
    return pdfPath;
  }

  async writeVisualAssets(docId, content) {
    const links = [];
    const visuals = Array.isArray(content.visuals) ? content.visuals : [];
    if (!visuals.length) return links;
    await this.ensureVaultFolder(this.settings.assetFolder);
    for (const visual of visuals) {
      const label = safeName(visual.label || `${visual.kind || 'visual'}-${visual.index}`, `visual-${visual.index}`);
      const assetPath = normalizePath(`${this.settings.assetFolder}/${docId}-${label}.png`);
      try {
        const response = await this.api(`/api/notes/${encodeURIComponent(docId)}/assets/${visual.index}`);
        const existing = this.app.vault.getAbstractFileByPath(assetPath);
        if (existing instanceof TFile) await this.app.vault.modifyBinary(existing, response.arrayBuffer);
        else await this.app.vault.createBinary(assetPath, response.arrayBuffer);
        links.push({ ...visual, path: assetPath });
      } catch (error) {
        console.warn('Failed to export note visual', visual, error);
      }
    }
    return links;
  }

  buildNoteMarkdown(docId, doc, note, pdfPath, visuals) {
    const content = note.content || {};
    const meta = doc.metadata || {};
    const title = meta.title || content.title || doc.filename || '논문 노트';
    const authors = asList(meta.authors || meta.author);
    const keywords = Array.from(new Set([...(content.keywords || []), 'paper']));
    const frontmatter = [
      '---',
      `title: ${yamlString(title)}`,
      `paper_workspace_id: ${yamlString(docId)}`,
      `source_pdf: ${yamlString(`[[${pdfPath}]]`)}`,
      `authors: [${authors.map(yamlString).join(', ')}]`,
      `tags: [${keywords.map((item) => yamlString(String(item).replace(/^#/, ''))).join(', ')}]`,
      `updated: ${yamlString(new Date().toISOString())}`,
      '---',
      '',
    ].join('\n');
    const visualMd = visuals.length ? visuals.map((visual) => [
      `### ${visual.label || `Figure/Table ${visual.index + 1}`}`,
      '',
      `![[${visual.path}]]`,
      '',
      visual.caption || '',
    ].join('\n')).join('\n\n') : '아직 내보낼 Figure/Table이 없습니다.';
    const generated = [
      GENERATED_START,
      `# ${title}`,
      '',
      `> [!abstract] 한 줄 요약\n> ${content.one_line_summary || content.summary || ''}`,
      '',
      '## 논문 요약', '', content.summary || '',
      '', '## 핵심 기여', '', markdownList(content.contributions),
      '', '## 핵심 방법', '', content.method_summary || '',
      '', '## 실험 및 결과', '', content.results_summary || '',
      '', '## 실험 흐름', '', (content.experiment_flow || []).map((item, index) => `${index + 1}. **가설** ${item.hypothesis || ''}\n   - 방법: ${item.method || ''}\n   - 결과: ${item.result || ''}`).join('\n') || '-',
      '', '## 한계와 향후 연구', '', content.limitations || '',
      '', '## 핵심 정리', '', markdownList(content.takeaways),
      '', '## 키워드', '', (content.keywords || []).map((item) => `#${String(item).replace(/^#/, '')}`).join(' · ') || '-',
      '', '## 용어집', '', (content.glossary || []).map((item) => `- **${item.term || ''}** — ${item.definition || ''}`).join('\n') || '-',
      '', '## Figure · Table', '', visualMd,
      '', '## 원문', '', `![[${pdfPath}]]`,
      GENERATED_END,
    ].join('\n');
    return { frontmatter, generated, title };
  }

  async exportNote(docId, quiet = false) {
    const [docResponse, noteResponse] = await Promise.all([
      this.api(`/api/library/${encodeURIComponent(docId)}`),
      this.api(`/api/notes/${encodeURIComponent(docId)}`),
    ]);
    const doc = docResponse.json;
    const note = noteResponse.json;
    if (!note.content) throw new Error(note.status === 'generating' ? '노트를 생성하는 중입니다.' : '생성된 노트가 없습니다.');
    const pdfPath = await this.getPaperPdfPath(doc, docId);
    const visuals = await this.writeVisualAssets(docId, note.content);
    await this.ensureVaultFolder(this.settings.noteFolder);
    const built = this.buildNoteMarkdown(docId, doc, note, pdfPath, visuals);
    const previousPath = this.settings.notePaths[docId];
    const notePath = previousPath || normalizePath(`${this.settings.noteFolder}/${safeName(built.title, docId)}.md`);
    const existing = this.app.vault.getAbstractFileByPath(notePath);
    if (existing instanceof TFile) {
      await this.app.vault.process(existing, (current) => {
        const start = current.indexOf(GENERATED_START);
        const end = current.indexOf(GENERATED_END);
        if (start >= 0 && end >= start) {
          return `${current.slice(0, start)}${built.generated}${current.slice(end + GENERATED_END.length)}`;
        }
        return `${current.trim()}\n\n${built.generated}\n`;
      });
    } else {
      await this.app.vault.create(notePath, `${built.frontmatter}${built.generated}\n`);
    }
    const noteFile = this.app.vault.getAbstractFileByPath(notePath);
    if (noteFile instanceof TFile) {
      const meta = doc.metadata || {};
      await this.app.fileManager.processFrontMatter(noteFile, (frontmatter) => {
        frontmatter.title = built.title;
        frontmatter.paper_workspace_id = docId;
        frontmatter.source_pdf = `[[${pdfPath}]]`;
        frontmatter.authors = asList(meta.authors || meta.author);
        frontmatter.tags = Array.from(new Set([...(note.content?.keywords || []), 'paper']));
        frontmatter.updated = new Date().toISOString();
      });
    }
    this.settings.notePaths[docId] = notePath;
    await this.saveSettings();
    if (!quiet) {
      new Notice('논문 노트와 시각 자료를 Vault에 동기화했습니다.');
      await this.app.workspace.openLinkText(notePath, '', false);
    }
    return notePath;
  }

  async exportCurrentNote() {
    if (this.currentDocId) {
      try { await this.exportNote(this.currentDocId); } catch (error) { new Notice(error.message); }
      return;
    }
    try {
      const response = await this.api('/api/notes');
      const notes = (response.json.notes || []).filter((item) => item.content);
      if (!notes.length) { new Notice('내보낼 생성 노트가 없습니다.'); return; }
      new PaperPicker(this.app, notes, (item) => this.exportNote(item.doc_id)).open();
    } catch (error) { new Notice(`노트 목록을 불러오지 못했습니다: ${error.message}`); }
  }

  async syncAllNotes() {
    const notice = new Notice('생성된 논문 노트를 동기화하는 중…', 0);
    try {
      const response = await this.api('/api/notes');
      const notes = (response.json.notes || []).filter((item) => item.content);
      let completed = 0;
      for (const note of notes) {
        try { await this.exportNote(note.doc_id, true); completed += 1; } catch (error) { console.warn(error); }
      }
      notice.hide();
      new Notice(`${completed}개 논문 노트를 Vault에 동기화했습니다.`);
    } catch (error) {
      notice.hide();
      new Notice(`노트 동기화 실패: ${error.message}`);
    }
  }

  async addSelectionToVocabulary(editor, file, docId) {
    const term = editor.getSelection().trim().replace(/\s+/g, ' ');
    const cursor = editor.getCursor('from');
    const contextEn = editor.getLine(cursor.line).trim() || term;
    await this.openVocabularyCard({
      doc_id: docId, term, context_en: contextEn, context_ko: '',
      paper_title: this.app.metadataCache.getFileCache(file)?.frontmatter?.title || file.basename,
      page_num: null, sync_anki: true,
    });
  }

  async openVocabularyCard(seed) {
    const notice = new Notice('문맥에 맞는 뜻을 생성하는 중…', 0);
    try {
      const suggestion = await this.api('/api/vocabulary/suggest', {
        method: 'POST', json: {
          doc_id: seed.doc_id, term: seed.term,
          context_en: seed.context_en || seed.term, context_ko: seed.context_ko || '',
        },
      });
      notice.hide();
      new VocabularyModal(this.app, {
        ...seed,
        meaning_ko: suggestion.json.meaning_ko || '',
        context_ko: suggestion.json.context_ko || seed.context_ko || '',
      }, async (payload) => {
        await this.api('/api/vocabulary', { method: 'POST', json: payload });
        new Notice('단어를 저장하고 Anki에 보냈습니다.');
      }).open();
    } catch (error) {
      notice.hide();
      new Notice(`단어 카드를 만들지 못했습니다: ${error.message}`);
    }
  }
};
