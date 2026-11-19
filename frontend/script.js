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

const MAX_CHARS = input.maxLength > 0 ? input.maxLength : 2000;
const SHOW_COUNTER_AT = 0.8;
const MSG_SESSAO_EXPIRADA = Api.MSG_SESSAO_EXPIRADA;
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
  notebooks.bloquear(sending); // não troca nem altera o notebook com uma pergunta em andamento

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
    const dados = await Api.chat(pergunta, notebookAtualId);
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

  // token expirado: volta para a tela de entrada sem perder o que foi digitado
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

// ---------- notebooks ----------

// A conversa é descartada ao entrar em outro notebook (o histórico persistente
// por notebook entra nos commits 11 a 14).
function limparConversa() {
  chat.querySelectorAll('.msg').forEach((n) => n.remove());
  emptyState.hidden = false;
  clearBtn.hidden = true;
  input.value = '';
  autosize();
  updateComposer();
}

let notebookAtualId = null; // o chat sempre pergunta dentro de um notebook

const docs = createDocuments({ api: Api, view: DocumentsView });
DocumentsView.ligar({
  escolher: (arquivos) => docs.enviar(arquivos),
  excluir: (id) => docs.excluir(id),
  atualizar: () => docs.atualizar(),
});

const notebooks = createNotebooks({
  api: Api,
  storage: window.sessionStorage,
  view: NotebooksView,
  onAbrir: (notebook) => {
    notebookAtualId = notebook.notebookId;
    docs.abrir(notebook.notebookId);
    limparConversa();
    chat.hidden = false;
    composer.hidden = false;
    if (window.matchMedia('(pointer: fine)').matches) input.focus();
  },
  onFechar: () => {
    notebookAtualId = null;
    docs.fechar();
    chat.hidden = true;
    composer.hidden = true;
    clearBtn.hidden = true;
  },
});

// ---------- autenticação ----------

// Depois do login quem decide o que aparece é o controlador dos notebooks:
// a lista, ou o notebook em que o usuário estava.
function showApp(user) {
  loginScreen.hidden = true;
  userEmail.textContent = user.email;
  userEmail.hidden = !user.email;
  logoutBtn.hidden = false;
  notebooks.iniciar(user.sub);
}

function showLogin(mensagem) {
  docs.fechar();
  notebooks.reset();
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
  loginBtn.disabled = true; // evita cliques duplos enquanto redireciona
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