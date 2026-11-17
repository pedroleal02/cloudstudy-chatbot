'use strict';

/*
 * Testes do controlador dos notebooks, com api, view e storage simulados
 * (sem DOM, sem rede). Requer Node 18+. Rodar:  node --test frontend/
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { createNotebooks, normalizarCampos, ErroDeFormulario } = require('./notebooks.js');
const { ApiError } = require('./api.js');

const nb = (id, nome = `Notebook ${id}`, descricao = '') => ({
  notebookId: id, nome, descricao, criadoEm: '2026-11-16T10:00:00.000Z', atualizadoEm: '2026-11-16T10:00:00.000Z',
});
const A = nb('A'.repeat(26), 'Inteligência Artificial', 'Aulas de IA');
const B = nb('B'.repeat(26), 'Sistemas Operacionais');

const esperar = () => new Promise((r) => setImmediate(r));

function montar({ lista = [A, B], obter, storageQuebrado = false } = {}) {
  const mem = new Map();
  const storage = storageQuebrado
    ? { getItem() { throw new Error('indisponível'); }, setItem() { throw new Error('indisponível'); }, removeItem() { throw new Error('indisponível'); } }
    : { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };

  const chamadas = [];
  const servidor = { lista: lista.map((n) => ({ ...n })) };
  const api = {
    listarNotebooks: async () => { chamadas.push(['listar']); if (servidor.falharListar) throw servidor.falharListar; return servidor.lista.map((n) => ({ ...n })); },
    obterNotebook: async (id) => {
      chamadas.push(['obter', id]);
      if (obter) return obter(id);
      const n = servidor.lista.find((x) => x.notebookId === id);
      if (!n) throw new ApiError('Notebook não encontrado.', { status: 404 });
      return { ...n };
    },
    criarNotebook: async (dados) => {
      chamadas.push(['criar', dados]);
      if (servidor.falharCriar) throw servidor.falharCriar;
      const novo = nb('C'.repeat(26), dados.nome, dados.descricao);
      servidor.lista.push(novo);
      return { ...novo };
    },
    atualizarNotebook: async (id, dados) => {
      chamadas.push(['atualizar', id, dados]);
      if (servidor.falharAtualizar) throw servidor.falharAtualizar;
      const n = servidor.lista.find((x) => x.notebookId === id);
      Object.assign(n, dados);
      return { ...n };
    },
    excluirNotebook: async (id) => {
      chamadas.push(['excluir', id]);
      if (servidor.falharExcluir) throw servidor.falharExcluir;
      servidor.lista = servidor.lista.filter((x) => x.notebookId !== id);
    },
  };

  const eventos = [];
  const view = {
    ligar(h) { view.h = h; },
    listaCarregando() { eventos.push(['carregando']); },
    listaErro(msg, tentar) { eventos.push(['erro', msg]); view.tentar = tentar; },
    mostrarLista(l) { eventos.push(['lista', l.map((n) => n.notebookId)]); },
    mostrarNotebook(n) { eventos.push(['notebook', n.notebookId]); },
    atualizarNotebook(n) { eventos.push(['atualizado', n.notebookId, n.nome]); },
    aviso(m) { eventos.push(['aviso', m]); },
    abrirFormulario(o) { view.formulario = o; eventos.push(['formulario', o.titulo]); },
    confirmarExclusao(o) { view.exclusao = o; eventos.push(['confirmar', o.nome]); },
    bloquear(v) { eventos.push(['bloquear', v]); },
    fecharTudo() { eventos.push(['fechar']); },
  };

  const gancho = [];
  const n = createNotebooks({
    api, storage, view,
    onAbrir: (x) => gancho.push(['abriu', x.notebookId]),
    onFechar: () => gancho.push(['fechou']),
  });
  return { n, api, view, eventos, gancho, chamadas, servidor, storage, mem };
}

const tipos = (eventos) => eventos.map((e) => e[0]);

// ---------- iniciar e restaurar ----------

test('iniciar carrega e mostra a lista', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  assert.deepEqual(t.eventos, [['carregando'], ['lista', [A.notebookId, B.notebookId]]]);
  assert.deepEqual(t.gancho, [['fechou']]);
});

test('lista vazia também vai para a view', async () => {
  const t = montar({ lista: [] });
  await t.n.iniciar('user-1');
  assert.deepEqual(t.eventos.at(-1), ['lista', []]);
});

test('iniciar volta ao notebook que estava aberto', async () => {
  const t = montar();
  t.mem.set('cloudstudy.notebook.user-1', B.notebookId);
  await t.n.iniciar('user-1');
  await esperar();
  assert.deepEqual(t.gancho, [['abriu', B.notebookId]]);
  assert.ok(tipos(t.eventos).includes('notebook'));
  assert.ok(!tipos(t.eventos).includes('lista'));
});

test('notebook lembrado que não existe mais cai na lista', async () => {
  const t = montar();
  t.mem.set('cloudstudy.notebook.user-1', 'Z'.repeat(26));
  await t.n.iniciar('user-1');
  assert.equal(t.eventos.at(-1)[0], 'lista');
  assert.deepEqual(t.gancho, [['fechou']]);
});

test('a seleção lembrada é de cada usuário', async () => {
  const t = montar();
  t.mem.set('cloudstudy.notebook.user-1', A.notebookId);
  await t.n.iniciar('user-2');
  assert.deepEqual(t.gancho, [['fechou']]);
});

test('storage indisponível não quebra nada', async () => {
  const t = montar({ storageQuebrado: true });
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.voltar();
  assert.equal(t.eventos.at(-1)[0], 'lista');
});

test('erro ao listar mostra a mensagem e permite tentar de novo', async () => {
  const t = montar();
  t.servidor.falharListar = new ApiError('Sem conexão.', { retryable: true });
  await t.n.iniciar('user-1');
  assert.deepEqual(t.eventos.at(-1), ['erro', 'Sem conexão.']);

  t.servidor.falharListar = null;
  await t.view.tentar();
  assert.equal(t.eventos.at(-1)[0], 'lista');
});

test('reset durante o carregamento descarta a resposta antiga', async () => {
  const t = montar();
  const carregando = t.n.iniciar('user-1');
  t.n.reset();
  await carregando;
  assert.deepEqual(tipos(t.eventos), ['carregando', 'fechar']);
  assert.deepEqual(t.gancho, []);
});

test('reset limpa o estado e fecha a tela', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.reset();
  assert.equal(t.eventos.at(-1)[0], 'fechar');
  t.n.editar(); // sem notebook aberto: nada acontece
  assert.equal(t.view.formulario, undefined);
});

// ---------- abrir e voltar ----------

test('abrir mostra o notebook, avisa o chat, lembra a escolha e atualiza pelo servidor', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.servidor.lista[0].nome = 'IA (renomeado no servidor)';
  await t.n.abrir(A.notebookId);

  assert.deepEqual(t.gancho.at(-1), ['abriu', A.notebookId]);
  assert.equal(t.mem.get('cloudstudy.notebook.user-1'), A.notebookId);
  assert.deepEqual(t.eventos.at(-1), ['atualizado', A.notebookId, 'IA (renomeado no servidor)']);
});

test('abrir um id que não está na lista não faz nada', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  const antes = t.eventos.length;
  await t.n.abrir('Z'.repeat(26));
  assert.equal(t.eventos.length, antes);
});

test('notebook excluído em outro lugar (404 ao abrir) volta para a lista com aviso', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.servidor.lista = [B];
  await t.n.abrir(A.notebookId);

  assert.deepEqual(t.eventos.slice(-2), [['lista', [B.notebookId]], ['aviso', 'Esse notebook não existe mais.']]);
  assert.equal(t.mem.has('cloudstudy.notebook.user-1'), false);
});

test('falha de rede ao conferir mantém o notebook aberto com os dados da lista', async () => {
  const t = montar({ obter: async () => { throw new ApiError('Sem conexão.', { retryable: true }); } });
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  assert.ok(!tipos(t.eventos).includes('aviso'));
  assert.equal(t.eventos.at(-1)[0], 'notebook');
});

test('resposta atrasada de um notebook não afeta o que já foi fechado', async () => {
  let liberar;
  const t = montar({ obter: () => new Promise((r) => { liberar = r; }) });
  await t.n.iniciar('user-1');
  const abrindo = t.n.abrir(A.notebookId);
  t.n.voltar();
  liberar({ ...A, nome: 'tarde demais' });
  await abrindo;
  assert.ok(!t.eventos.some((e) => e[0] === 'atualizado'));
});

test('voltar fecha o notebook, esquece a escolha e mostra a lista', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.voltar();
  assert.equal(t.eventos.at(-1)[0], 'lista');
  assert.deepEqual(t.gancho.at(-1), ['fechou']);
  assert.equal(t.mem.has('cloudstudy.notebook.user-1'), false);
});

test('a view pode abrir um notebook pelo clique no cartão', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.view.h.abrir(B.notebookId);
  await esperar();
  assert.deepEqual(t.gancho.at(-1), ['abriu', B.notebookId]);
});

// ---------- criar ----------

test('criar: valida, chama a API, soma à lista e abre o novo notebook', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.n.novo();
  assert.equal(t.view.formulario.titulo, 'Novo notebook');
  assert.equal(t.view.formulario.nome, '');

  await t.view.formulario.aoSalvar({ nome: '  Criptografia ', descricao: ' RSA e AES ' });
  await esperar();

  assert.deepEqual(t.chamadas.find((c) => c[0] === 'criar'), ['criar', { nome: 'Criptografia', descricao: 'RSA e AES' }]);
  assert.deepEqual(t.gancho.at(-1), ['abriu', 'C'.repeat(26)]);

  t.n.voltar();
  assert.deepEqual(t.eventos.at(-1)[1], [A.notebookId, B.notebookId, 'C'.repeat(26)]);
});

test('criar com dados inválidos não chama a API', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.n.novo();
  for (const valores of [{ nome: '' }, { nome: '   ' }, { nome: 'x'.repeat(81) }, { nome: 'ok', descricao: 'x'.repeat(501) }]) {
    await assert.rejects(t.view.formulario.aoSalvar(valores), (e) => e instanceof ErroDeFormulario);
  }
  assert.ok(!t.chamadas.some((c) => c[0] === 'criar'));
});

test('criar: erro da API volta para o formulário e não altera a lista', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  t.servidor.falharCriar = new ApiError('Muitas requisições.', { status: 429, retryable: true });
  t.n.novo();
  await assert.rejects(t.view.formulario.aoSalvar({ nome: 'X' }), /Muitas requisições/);
  t.n.voltar();
  assert.deepEqual(t.eventos.at(-1)[1], [A.notebookId, B.notebookId]);
});

// ---------- editar ----------

test('editar: formulário vem preenchido e o salvamento atualiza a tela e a lista', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.editar();
  assert.equal(t.view.formulario.nome, A.nome);
  assert.equal(t.view.formulario.descricao, 'Aulas de IA');

  await t.view.formulario.aoSalvar({ nome: 'Machine Learning', descricao: 'Aulas de IA' });
  assert.deepEqual(t.chamadas.find((c) => c[0] === 'atualizar'), ['atualizar', A.notebookId, { nome: 'Machine Learning', descricao: 'Aulas de IA' }]);
  assert.deepEqual(t.eventos.at(-1), ['atualizado', A.notebookId, 'Machine Learning']);

  t.n.voltar();
  assert.equal(t.eventos.at(-1)[0], 'lista');
});

test('editar sem mudar nada não chama a API', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.editar();
  await t.view.formulario.aoSalvar({ nome: `  ${A.nome} `, descricao: A.descricao });
  assert.ok(!t.chamadas.some((c) => c[0] === 'atualizar'));
});

test('editar: nome vazio é recusado antes de chamar a API', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.editar();
  await assert.rejects(t.view.formulario.aoSalvar({ nome: ' ', descricao: '' }), ErroDeFormulario);
  assert.ok(!t.chamadas.some((c) => c[0] === 'atualizar'));
});

test('editar um notebook que já foi excluído (404) volta para a lista com aviso', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.editar();
  t.servidor.falharAtualizar = new ApiError('Notebook não encontrado.', { status: 404 });
  await t.view.formulario.aoSalvar({ nome: 'Novo nome' });
  assert.deepEqual(t.eventos.slice(-2), [['lista', [B.notebookId]], ['aviso', 'Esse notebook não existe mais.']]);
});

test('editar: outros erros da API voltam para o formulário', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.editar();
  t.servidor.falharAtualizar = new ApiError('Erro interno.', { status: 500, retryable: true });
  await assert.rejects(t.view.formulario.aoSalvar({ nome: 'Outro' }), /Erro interno/);
  assert.equal(t.eventos.at(-1)[0] === 'atualizado', false);
});

// ---------- excluir ----------

test('excluir: pede confirmação com o nome e, confirmado, apaga e volta para a lista', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.excluir();
  assert.deepEqual(t.eventos.at(-1), ['confirmar', A.nome]);
  assert.ok(!t.chamadas.some((c) => c[0] === 'excluir')); // só pedir não apaga

  await t.view.exclusao.aoConfirmar();
  assert.deepEqual(t.chamadas.find((c) => c[0] === 'excluir'), ['excluir', A.notebookId]);
  assert.deepEqual(t.eventos.slice(-2), [['lista', [B.notebookId]], ['aviso', `Notebook "${A.nome}" excluído.`]]);
  assert.equal(t.mem.has('cloudstudy.notebook.user-1'), false);
});

test('excluir: 404 conta como sucesso (já não existia)', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.excluir();
  t.servidor.falharExcluir = new ApiError('Notebook não encontrado.', { status: 404 });
  await t.view.exclusao.aoConfirmar();
  assert.equal(t.eventos.at(-2)[0], 'lista');
});

test('excluir: erro da API mantém o notebook e mostra o erro no diálogo', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.excluir();
  t.servidor.falharExcluir = new ApiError('Erro interno.', { status: 500, retryable: true });
  await assert.rejects(t.view.exclusao.aoConfirmar(), /Erro interno/);
  assert.equal(t.mem.get('cloudstudy.notebook.user-1'), A.notebookId);
  assert.ok(!tipos(t.eventos).slice(-3).includes('aviso'));
});

// ---------- bloqueio durante uma pergunta ----------

test('bloqueado: não navega, não cria, não edita e não exclui', async () => {
  const t = montar();
  await t.n.iniciar('user-1');
  await t.n.abrir(A.notebookId);
  t.n.bloquear(true);
  assert.deepEqual(t.eventos.at(-1), ['bloquear', true]);

  const antes = t.eventos.length;
  t.n.voltar();
  t.n.novo();
  t.n.editar();
  t.n.excluir();
  assert.equal(t.eventos.length, antes);

  t.n.bloquear(false);
  t.n.voltar();
  assert.equal(t.eventos.at(-1)[0], 'lista');
});

// ---------- validação ----------

test('normalizarCampos limpa espaços e caracteres de controle', () => {
  assert.deepEqual(normalizarCampos({ nome: '  Cripto\u0000grafia\u0007 ', descricao: undefined }), { nome: 'Criptografia', descricao: '' });
  assert.deepEqual(normalizarCampos({ nome: 'x'.repeat(80), descricao: 'y'.repeat(500) }).nome.length, 80);
});