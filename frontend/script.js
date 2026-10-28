'use strict';

const chat = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const clearBtn = document.getElementById('clear-btn');
const counter = document.getElementById('char-count');
const emptyState = document.getElementById('empty-state');

// API_URL vem do config.js (sem barra no final)
const API_BASE = (typeof API_URL === 'string' ? API_URL : '').replace(/\/+$/, '');
// um pouco acima do timeout da Lambda (30 s), para a Lambda falhar primeiro
const TIMEOUT_MS = 35000;
const MAX_CHARS = input.maxLength > 0 ? input.maxLength : 2000;
const SHOW_COUNTER_AT = 0.8;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

let sending = false;
let messageCount = 0;

// ---------- utilidades ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const scrollBehavior = () => (reducedMotion.matches ? 'auto' : 'smooth');

function scrollToBottom() {
  window.scrollTo({ top: document.documentElement.scrollHeight, behavior: scrollBehavior() });
}

// ---------- API ----------

class ApiError extends Error {
  constructor(message, { status = 0, retryable = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryable = retryable;
  }
}

async function lerJson(response) {
  try {
    const data = await response.json();
    return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
  } catch {
    return {}; // resposta sem JSON válido
  }
}

function erroDaApi(status, data) {
  const ref = typeof data.id_requisicao === 'string' ? ` (ref: ${data.id_requisicao.slice(0, 8)})` : '';
  const retryable = status === 429 || status >= 500;

  if (typeof data.erro === 'string' && data.erro) {
    return new ApiError(data.erro + ref, { status, retryable });
  }
  if (status === 429) {
    return new ApiError('Muitas requisições. Aguarde alguns segundos e tente de novo.', { status, retryable });
  }
  if (status >= 500) {
    return new ApiError('O serviço está indisponível no momento. Tente novamente em instantes.', { status, retryable });
  }
  return new ApiError('Não foi possível processar a solicitação.', { status, retryable });
}

// Chama o backend (POST /chat) e devolve { resposta, referencias }
async function sendToApi(pergunta) {
  if (!API_BASE) {
    throw new ApiError('A URL da API não está configurada. Verifique o config.js.');
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pergunta }), // a Lambda espera "pergunta"
      signal: controller.signal,
    });

    const data = await lerJson(response);

    if (!response.ok) throw erroDaApi(response.status, data);

    if (typeof data.resposta !== 'string' || !data.resposta.trim()) {
      throw new ApiError('A API não devolveu uma resposta.', { status: response.status, retryable: true });
    }
    return data;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err.name === 'AbortError') {
      throw new ApiError('A resposta demorou demais. Tente novamente.', { retryable: true });
    }
    // falha de rede ou CORS
    throw new ApiError('Não foi possível conectar ao servidor. Verifique sua conexão e tente de novo.', { retryable: true });
  } finally {
    clearTimeout(timeoutId);
  }
}

// ---------- renderização ----------

function addUser(text) {
  const node = el('article', 'msg msg-user', text);
  chat.appendChild(node);
  return node;
}

function addLoading() {
  const node = el('div', 'msg msg-bot loading');
  const dots = el('span', 'dots');
  dots.setAttribute('aria-hidden', 'true');
  dots.append(el('i'), el('i'), el('i'));
  node.append(dots, el('span', '', 'Consultando os materiais…'));
  chat.appendChild(node);
  return node;
}

function citeButton(numero, uid) {
  const btn = el('button', 'cite', String(numero));
  btn.type = 'button';
  btn.setAttribute('aria-label', `Ver fonte ${numero}`);
  btn.addEventListener('click', () => {
    const item = document.getElementById(`fonte-${uid}-${numero}`);
    if (!item) return;
    const details = item.closest('details');
    if (details) details.open = true;
    item.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' });
    item.classList.remove('flash');
    void item.offsetWidth; // reinicia a animação
    item.classList.add('flash');
  });
  return btn;
}

// Troca "[1]" ou "[1, 2]" por marcadores clicáveis, mas só quando a fonte existe
function appendWithCitations(container, texto, idsValidos, uid) {
  const re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
  let ultimo = 0;
  let m;

  while ((m = re.exec(texto)) !== null) {
    const numeros = m[1].split(',').map((s) => Number(s.trim()));
    if (!numeros.every((n) => idsValidos.has(n))) continue; // mantém como texto

    container.appendChild(document.createTextNode(texto.slice(ultimo, m.index)));
    numeros.forEach((n) => container.appendChild(citeButton(n, uid)));
    ultimo = m.index + m[0].length;
  }
  container.appendChild(document.createTextNode(texto.slice(ultimo)));
}

function buildSources(refs, uid) {
  const details = el('details', 'fontes');
  details.appendChild(el('summary', '', `Fontes (${refs.length})`));

  const list = el('ol');
  refs.forEach((r) => {
    const item = el('li');
    item.id = `fonte-${uid}-${r.id}`;
    item.value = r.id; // a numeração da lista bate com os marcadores
    item.appendChild(el('strong', 'fonte-arquivo', String(r.arquivo || 'documento')));
    if (r.trecho) item.appendChild(el('p', 'trecho', String(r.trecho)));
    list.appendChild(item);
  });

  details.appendChild(list);
  return details;
}

function addBot(resposta, referencias) {
  const uid = ++messageCount;
  const refs = (Array.isArray(referencias) ? referencias : []).filter((r) => r && Number.isInteger(r.id));
  const idsValidos = new Set(refs.map((r) => r.id));

  const node = el('article', 'msg msg-bot');
  const answer = el('div', 'answer');

  resposta
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .forEach((paragrafo) => {
      const p = el('p');
      appendWithCitations(p, paragrafo, idsValidos, uid);
      answer.appendChild(p);
    });

  node.appendChild(answer);
  if (refs.length > 0) node.appendChild(buildSources(refs, uid));

  chat.appendChild(node);
  return node;
}

function addError(err, pergunta) {
  const known = err instanceof ApiError;
  const node = el('div', 'msg msg-error');
  node.setAttribute('role', 'alert');
  node.appendChild(el('span', '', known ? err.message : 'Algo deu errado. Tente novamente.'));

  if (!known || err.retryable) {
    const retry = el('button', 'retry', 'Tentar de novo');
    retry.type = 'button';
    retry.addEventListener('click', () => {
      if (sending) return;
      node.remove();
      ask(pergunta, { echo: false });
    });
    node.appendChild(retry);
  }

  chat.appendChild(node);
  return node;
}

// ---------- fluxo principal ----------

function updateComposer() {
  const len = input.value.length;
  sendBtn.disabled = sending || input.value.trim().length === 0;
  clearBtn.disabled = sending;

  const mostrar = len >= MAX_CHARS * SHOW_COUNTER_AT;
  counter.hidden = !mostrar;
  if (mostrar) {
    counter.textContent = `${len} / ${MAX_CHARS}`;
    counter.classList.toggle('near-limit', len >= MAX_CHARS);
  }
}

function autosize() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

async function ask(pergunta, { echo = true } = {}) {
  if (sending) return;
  sending = true;
  updateComposer();

  // erros antigos deixam de oferecer "Tentar de novo"
  chat.querySelectorAll('.msg-error .retry').forEach((b) => b.remove());

  emptyState.hidden = true;
  clearBtn.hidden = false;

  if (echo) addUser(pergunta);
  const loading = addLoading();
  scrollToBottom();

  try {
    const dados = await sendToApi(pergunta);
    loading.remove();
    const node = addBot(dados.resposta, dados.referencias);
    node.scrollIntoView({ behavior: scrollBehavior(), block: 'start' });
  } catch (err) {
    loading.remove();
    addError(err, pergunta);
    scrollToBottom();
  } finally {
    sending = false;
    updateComposer();
    // no celular, focar abriria o teclado por cima da resposta
    if (window.matchMedia('(pointer: fine)').matches) input.focus();
  }
}

// ---------- eventos ----------

input.addEventListener('input', () => {
  autosize();
  updateComposer();
});

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { cancelable: true }));
  }
});

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const texto = input.value.trim();
  if (!texto || sending) return;

  input.value = '';
  autosize();
  updateComposer();
  ask(texto);
});

clearBtn.addEventListener('click', () => {
  if (sending) return;
  chat.querySelectorAll('.msg').forEach((n) => n.remove());
  emptyState.hidden = false;
  clearBtn.hidden = true;
  window.scrollTo({ top: 0, behavior: scrollBehavior() });
  input.focus();
});

autosize();
updateComposer();
if (window.matchMedia('(pointer: fine)').matches) input.focus();