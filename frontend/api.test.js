'use strict';

/*
 * Testes do cliente da API. Nenhuma chamada real: fetch é simulado (sem custo, sem rede).
 * Requer Node 18+. Rodar:  node --test frontend/
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { createApi, ApiError } = require('./api.js');

const BASE = 'https://api.exemplo.com';

function resposta(status, corpo) {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => {
      if (corpo === undefined) throw new SyntaxError('sem corpo');
      return corpo;
    },
  };
}

function montar({ token = 'token-123', fetchFn, baseUrl = BASE, timeoutMs } = {}) {
  const chamadas = [];
  const eventos = { sessaoExpirada: 0 };
  const falso = fetchFn || (async () => resposta(200, {}));
  const api = createApi({
    baseUrl,
    getToken: () => token,
    onSessaoExpirada: () => { eventos.sessaoExpirada += 1; },
    fetchFn: async (url, opcoes) => {
      chamadas.push({ url, ...opcoes, corpo: opcoes.body ? JSON.parse(opcoes.body) : undefined });
      return falso(url, opcoes);
    },
    timeoutMs,
  });
  return { api, chamadas, eventos };
}

const NB = { notebookId: '01JD5ZK3Q8X7M2N4P6R8T0V2WY', nome: 'IA', descricao: '', criadoEm: 'x', atualizadoEm: 'x' };

// ---------- chat ----------

test('chat envia POST /chat com token e a pergunta', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(200, { resposta: 'Oi [1].', referencias: [] }) });
  const dados = await api.chat('O que é recursão?');
  assert.equal(dados.resposta, 'Oi [1].');
  assert.equal(chamadas[0].url, `${BASE}/chat`);
  assert.equal(chamadas[0].method, 'POST');
  assert.equal(chamadas[0].headers.Authorization, 'Bearer token-123');
  assert.equal(chamadas[0].headers['Content-Type'], 'application/json');
  assert.deepEqual(chamadas[0].corpo, { pergunta: 'O que é recursão?' });
});

test('chat sem texto de resposta vira erro que pode ser repetido', async () => {
  for (const corpo of [{}, { resposta: '' }, { resposta: '   ' }, { resposta: 5 }]) {
    const { api } = montar({ fetchFn: async () => resposta(200, corpo) });
    await assert.rejects(api.chat('oi'), (e) => e instanceof ApiError && e.retryable);
  }
});

// ---------- token e sessão ----------

test('sem token: avisa a sessão expirada, falha com 401 e não chama a rede', async () => {
  const { api, chamadas, eventos } = montar({ token: null });
  await assert.rejects(api.listarNotebooks(), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(eventos.sessaoExpirada, 1);
  assert.equal(chamadas.length, 0);
});

test('401 do servidor avisa a sessão expirada', async () => {
  const { api, eventos } = montar({ fetchFn: async () => resposta(401, { message: 'Unauthorized' }) });
  await assert.rejects(api.listarNotebooks(), (e) => e.status === 401 && e.message === api.MSG_SESSAO_EXPIRADA);
  assert.equal(eventos.sessaoExpirada, 1);
});

test('URL da API ausente: erro claro e nenhuma chamada', async () => {
  const { api, chamadas } = montar({ baseUrl: '' });
  await assert.rejects(api.listarNotebooks(), /URL da API/);
  assert.equal(chamadas.length, 0);
});

test('barra no final da URL base é ignorada', async () => {
  const { api, chamadas } = montar({ baseUrl: `${BASE}//`, fetchFn: async () => resposta(200, { notebooks: [] }) });
  await api.listarNotebooks();
  assert.equal(chamadas[0].url, `${BASE}/notebooks`);
});

// ---------- notebooks ----------

test('listar: GET /notebooks sem corpo e sem Content-Type', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(200, { notebooks: [NB] }) });
  assert.deepEqual(await api.listarNotebooks(), [NB]);
  assert.equal(chamadas[0].method, 'GET');
  assert.equal(chamadas[0].body, undefined);
  assert.equal(chamadas[0].headers['Content-Type'], undefined);
  assert.equal(chamadas[0].headers.Authorization, 'Bearer token-123');
});

test('listar descarta itens malformados e rejeita resposta sem lista', async () => {
  const sujo = { notebooks: [NB, null, {}, { notebookId: 1, nome: 'x' }, { notebookId: 'a' }, 'texto'] };
  const { api } = montar({ fetchFn: async () => resposta(200, sujo) });
  assert.deepEqual(await api.listarNotebooks(), [NB]);

  for (const corpo of [{}, { notebooks: 'x' }, { notebooks: null }]) {
    const { api: api2 } = montar({ fetchFn: async () => resposta(200, corpo) });
    await assert.rejects(api2.listarNotebooks(), (e) => e instanceof ApiError && e.retryable);
  }
});

test('obter: GET /notebooks/{id} com o id codificado na URL', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(200, { notebook: NB }) });
  assert.deepEqual(await api.obterNotebook('a/b?c#d'), NB);
  assert.equal(chamadas[0].url, `${BASE}/notebooks/a%2Fb%3Fc%23d`);
});

test('criar: POST /notebooks com nome e descrição', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(201, { notebook: NB }) });
  assert.deepEqual(await api.criarNotebook({ nome: 'IA', descricao: 'aulas' }), NB);
  assert.equal(chamadas[0].method, 'POST');
  assert.equal(chamadas[0].url, `${BASE}/notebooks`);
  assert.deepEqual(chamadas[0].corpo, { nome: 'IA', descricao: 'aulas' });
});

test('atualizar: PATCH /notebooks/{id} só com os campos enviados', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(200, { notebook: { ...NB, nome: 'SO' } }) });
  const nb = await api.atualizarNotebook(NB.notebookId, { nome: 'SO' });
  assert.equal(nb.nome, 'SO');
  assert.equal(chamadas[0].method, 'PATCH');
  assert.equal(chamadas[0].url, `${BASE}/notebooks/${NB.notebookId}`);
  assert.deepEqual(chamadas[0].corpo, { nome: 'SO' });
});

test('excluir: DELETE /notebooks/{id}, 204 sem corpo é sucesso', async () => {
  const { api, chamadas } = montar({ fetchFn: async () => resposta(204) });
  assert.equal(await api.excluirNotebook(NB.notebookId), undefined);
  assert.equal(chamadas[0].method, 'DELETE');
  assert.equal(chamadas[0].url, `${BASE}/notebooks/${NB.notebookId}`);
});

test('criar/obter/atualizar rejeitam resposta sem notebook', async () => {
  const { api } = montar({ fetchFn: async () => resposta(200, { notebook: { nome: 'sem id' } }) });
  await assert.rejects(api.criarNotebook({ nome: 'x' }), ApiError);
  await assert.rejects(api.obterNotebook(NB.notebookId), ApiError);
  await assert.rejects(api.atualizarNotebook(NB.notebookId, { nome: 'x' }), ApiError);
});

// ---------- erros ----------

test('erro 400/404 usa a mensagem da API e não permite repetir', async () => {
  for (const [status, erro] of [[400, "O campo 'nome' não pode estar vazio."], [404, 'Notebook não encontrado.']]) {
    const { api } = montar({ fetchFn: async () => resposta(status, { erro }) });
    await assert.rejects(api.obterNotebook(NB.notebookId), (e) => e.status === status && e.message === erro && !e.retryable);
  }
});

test('429 e 5xx podem ser repetidos; id_requisicao aparece como referência curta', async () => {
  const { api } = montar({ fetchFn: async () => resposta(500, { erro: 'Erro interno.', id_requisicao: 'abcdef12-3456-7890' }) });
  await assert.rejects(api.listarNotebooks(), (e) => e.retryable && e.message === 'Erro interno. (ref: abcdef12)');

  const { api: api429 } = montar({ fetchFn: async () => resposta(429, {}) });
  await assert.rejects(api429.listarNotebooks(), (e) => e.retryable && /Muitas requisições/.test(e.message));

  const { api: api502 } = montar({ fetchFn: async () => resposta(502) });
  await assert.rejects(api502.listarNotebooks(), (e) => e.retryable && /indisponível/.test(e.message));
});

test('erro sem JSON válido e sem mensagem cai numa mensagem genérica', async () => {
  const { api } = montar({ fetchFn: async () => resposta(403) });
  await assert.rejects(api.listarNotebooks(), (e) => e.status === 403 && /Não foi possível processar/.test(e.message));
});

test('falha de rede ou CORS vira erro que pode ser repetido', async () => {
  const { api } = montar({ fetchFn: async () => { throw new TypeError('Failed to fetch'); } });
  await assert.rejects(api.listarNotebooks(), (e) => e instanceof ApiError && e.retryable && /conectar/.test(e.message));
});

test('timeout aborta a requisição', async () => {
  const lento = (url, opcoes) => new Promise((_, rejeitar) => {
    opcoes.signal.addEventListener('abort', () => rejeitar(Object.assign(new Error('abort'), { name: 'AbortError' })));
  });
  const { api } = montar({ fetchFn: lento, timeoutMs: 20 });
  await assert.rejects(api.listarNotebooks(), (e) => e.retryable && /demorou demais/.test(e.message));
});