'use strict';

const chat = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const clearBtn = document.getElementById('clear-btn');
const counter = document.getElementById('char-count');
const emptyState = document.getElementById('empty-state');
const composer = document.querySelector('.composer');
const loginScreen = document.getElementById('login-screen');
const loginBtn = document.getElementById('login-btn');
const loginError = document.getElementById('login-error');
const logoutBtn = document.getElementById('logout-btn');
const userEmail = document.getElementById('user-email');

const API_BASE = (typeof API_URL === 'string' ? API_URL : '').replace(/\/+$/, '');
const TIMEOUT_MS = 35000;
const MAX_CHARS = input.maxLength > 0 ? input.maxLength : 2000;
const SHOW_COUNTER_AT = 0.8;
const MSG_SESSAO_EXPIRADA = 'Sua sessão expirou. Entre de novo para continuar.';
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

let sending = false;
let messageCount = 0;

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
    return {};
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

async function sendToApi(pergunta) {
  if (!API_BASE) {
    throw new ApiError('A URL da API não está configurada. Verifique o config.js.');
  }

  const token = Auth.getIdToken();
  if (!token) {
    showLogin(MSG_SESSAO_EXPIRADA);
    throw new ApiError(MSG_SESSAO_EXPIRADA, { status: 401 });
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ pergunta }),
      signal: controller.signal,
    });

    const data = await lerJson(response);

    if (response.status === 401) {
      showLogin(MSG_SESSAO_EXPIRADA);
      throw new ApiError(MSG_SESSAO_EXPIRADA, { status: 401 });
    }
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
    throw new ApiError('Não foi possível conectar ao servidor. Verifique sua conexão e tente de novo.', { retryable: true });
  } finally {
    clearTimeout(timeoutId);
  }
}

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
    void item.offsetWidth;
    item.classList.add('flash');
  });
  return btn;
}

function appendWithCitations(container, texto, idsValidos, uid) {
  const re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
  let ultimo = 0;
  let m;

  while ((m = re.exec(texto)) !== null) {
    const numeros = m[1].split(',').map((s) => Number(s.trim()));
    if (!numeros.every((n) => idsValidos.has(n))) continue;

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
    item.value = r.id;
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
    if (window.matchMedia('(pointer: fine)').matches) input.focus();
  }
}


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

  if (!Auth.getUser()) {
    showLogin(MSG_SESSAO_EXPIRADA);
    return;
  }

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

function showApp(user) {
  loginScreen.hidden = true;
  chat.hidden = false;
  composer.hidden = false;
  userEmail.textContent = user.email;
  userEmail.hidden = !user.email;
  logoutBtn.hidden = false;
  autosize();
  updateComposer();
  if (window.matchMedia('(pointer: fine)').matches) input.focus();
}

function showLogin(mensagem) {
  chat.hidden = true;
  composer.hidden = true;
  clearBtn.hidden = true;
  logoutBtn.hidden = true;
  userEmail.hidden = true;
  loginScreen.hidden = false;
  loginBtn.disabled = false;
  loginError.textContent = mensagem || '';
  loginError.hidden = !mensagem;
}

loginBtn.addEventListener('click', async () => {
  loginBtn.disabled = true;
  try {
    await Auth.login();
  } catch (err) {
    showLogin(err.message);
  }
});

logoutBtn.addEventListener('click', () => Auth.logout());

async function boot() {
  if (!Auth.configurado()) {
    showLogin('O login ainda não foi configurado (COGNITO_CONFIG em config.js).');
    return;
  }
  const retorno = await Auth.tratarRetorno();
  const user = Auth.getUser();
  if (user) showApp(user);
  else showLogin(retorno.status === 'erro' ? retorno.mensagem : '');
}

boot();