'use strict';

/*
 * Testes do controlador de documentos e das chamadas de documentos da API.
 * Nenhuma chamada real: API, S3 e relógio são simulados (sem custo, sem rede).
 * Requer Node 18+. Rodar:  node --test frontend/
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { createDocuments, validarArquivo, formatarTamanho, resumir } = require('./documents.js');
const { createApi, ApiError } = require('./api.js');

const MB = 1024 * 1024;
const arq = (name, size = 1000) => ({ name, size });
const doc = (id, status, extra = {}) => ({
  documentoId: id, nome: `${id}.pdf`, tipo: 'pdf', tamanhoBytes: 1000, status, criadoEm: `2026-11-20T10:00:0${id.length}Z`, ...extra,
});
const esperar = () => new Promise((r) => setImmediate(r));

function montar({ lista = [], api = {}, oculta = false } = {}) {
  const chamadas = [];
  const agendados = [];
  const view = {
    ultimo: null, avisos: [], ocupadoLog: [], falhas: [], visivel: false,
    mostrar() { this.visivel = true; }, esconder() { this.visivel = false; },
    carregando() {}, falha(msg, tentar) { this.falhas.push({ msg, tentar }); },
    aviso(texto, opcoes) { this.avisos.push({ texto, ...opcoes }); },
    ocupado(v) { this.ocupadoLog.push(v); },
    render(m) { this.ultimo = m; },
  };
  const falsa = {
    listarDocumentos: async () => lista,
    solicitarUpload: async (nb, { nome, tamanhoBytes }) => ({
      documento: doc('d1', 'PENDENTE', { nome, tamanhoBytes }), upload: { url: 'https://s3.exemplo', campos: { key: 'k' } },
    }),
    enviarArquivo: async () => {},
    confirmarDocumento: async (nb, id) => doc(id, 'INDEXANDO'),
    excluirDocumento: async () => {},
    ...api,
  };
  const wrapped = Object.fromEntries(Object.entries(falsa).map(([k, f]) => [k, async (...a) => { chamadas.push([k, ...a]); return f(...a); }]));
  const ctrl = createDocuments({
    api: wrapped, view,
    setTimeoutFn: (fn, ms) => { agendados.push({ fn, ms }); return agendados.length; },
    clearTimeoutFn: () => {},
    isHidden: () => oculta,
  });
  return { ctrl, view, chamadas, agendados };
}

test('validarArquivo aceita pdf/md/txt e recusa o resto', () => {
  assert.equal(validarArquivo(arq('aula.PDF')), null);
  assert.equal(validarArquivo(arq('notas.md')), null);
  assert.equal(validarArquivo(arq('x.txt', 50 * MB)), null);
  assert.match(validarArquivo(arq('x.exe')), /tipo não aceito/);
  assert.match(validarArquivo(arq('semextensao')), /tipo não aceito/);
  assert.match(validarArquivo(arq('x.pdf', 0)), /vazio/);
  assert.match(validarArquivo(arq('x.pdf', 50 * MB + 1)), /50 MB/);
  assert.match(validarArquivo(arq(`${'a'.repeat(256)}.pdf`)), /longo demais/);
  assert.match(validarArquivo({}), /inválido/);
  assert.match(validarArquivo(null), /inválido/);
});

test('formatarTamanho e resumir', () => {
  assert.equal(formatarTamanho(512), '512 B');
  assert.equal(formatarTamanho(1536), '1,5 KB');
  assert.equal(formatarTamanho(5 * MB), '5,0 MB');
  assert.equal(resumir([]), '');
  assert.equal(
    resumir([{ tom: 'ok' }, { tom: 'andamento' }, { tom: 'erro' }]),
    '3 documentos · 1 pronto · 1 em processamento · 1 com problema'
  );
});

test('abrir lista os documentos e mostra rótulos por estado', async () => {
  const { ctrl, view } = montar({ lista: [doc('a', 'PRONTO'), doc('bb', 'ERRO'), doc('ccc', 'PENDENTE')] });
  await ctrl.abrir('nb1');
  assert.equal(view.visivel, true);
  assert.deepEqual(view.ultimo.itens.map((i) => [i.rotulo, i.tom, i.podeExcluir]), [
    ['Pronto', 'ok', true], ['Erro ao indexar', 'erro', true], ['Envio incompleto', 'aviso', true],
  ]);
});

test('falha ao carregar mostra erro com "tentar de novo"', async () => {
  const { ctrl, view } = montar({ api: { listarDocumentos: async () => { throw new ApiError('Sem rede.'); } } });
  await ctrl.abrir('nb1');
  assert.match(view.falhas[0].msg, /Sem rede/);
  assert.equal(typeof view.falhas[0].tentar, 'function');
});

test('envio completo: registra, manda ao S3, confirma e vira "Indexando"', async () => {
  const { ctrl, view, chamadas } = montar({ lista: [doc('d1', 'INDEXANDO', { nome: 'aula.pdf' })] });
  await ctrl.abrir('nb1');
  await ctrl.enviar([arq('aula.pdf', 2000)]);
  assert.deepEqual(chamadas.map((c) => c[0]), ['listarDocumentos', 'solicitarUpload', 'enviarArquivo', 'confirmarDocumento', 'listarDocumentos']);
  assert.deepEqual(chamadas[1].slice(1), ['nb1', { nome: 'aula.pdf', tamanhoBytes: 2000 }]);
  assert.equal(view.ultimo.itens[0].rotulo, 'Indexando…');
  assert.deepEqual(view.ocupadoLog.slice(-2), [true, false]);
  assert.equal(view.avisos.at(-1).texto, null);
});

test('arquivo inválido não chama a API e gera aviso de erro', async () => {
  const { ctrl, view, chamadas } = montar();
  await ctrl.abrir('nb1');
  await ctrl.enviar([arq('virus.exe'), arq('vazio.pdf', 0)]);
  assert.equal(chamadas.filter((c) => c[0] === 'solicitarUpload').length, 0);
  const aviso = view.avisos.at(-1);
  assert.equal(aviso.erro, true);
  assert.match(aviso.texto, /virus\.exe: tipo não aceito/);
  assert.match(aviso.texto, /vazio\.pdf: o arquivo está vazio/);
});

test('falha no S3 apaga o registro criado e avisa', async () => {
  const { ctrl, view, chamadas } = montar({ api: { enviarArquivo: async () => { throw new ApiError('O armazenamento recusou o arquivo.'); } } });
  await ctrl.abrir('nb1');
  await ctrl.enviar([arq('aula.pdf')]);
  assert.deepEqual(chamadas.find((c) => c[0] === 'excluirDocumento').slice(1), ['nb1', 'd1']);
  assert.equal(chamadas.some((c) => c[0] === 'confirmarDocumento'), false);
  assert.match(view.avisos.at(-1).texto, /aula\.pdf: O armazenamento recusou/);
  assert.equal(view.ultimo.itens.length, 0);
});

test('um arquivo com problema não impede os demais', async () => {
  let n = 0;
  const { ctrl, chamadas } = montar({
    api: {
      solicitarUpload: async (nb, { nome }) => {
        n += 1;
        if (n === 1) throw new ApiError('Cota excedida.');
        return { documento: doc('d2', 'PENDENTE', { nome }), upload: { url: 'u', campos: {} } };
      },
    },
  });
  await ctrl.abrir('nb1');
  await ctrl.enviar([arq('a.pdf'), arq('b.pdf')]);
  assert.equal(chamadas.filter((c) => c[0] === 'confirmarDocumento').length, 1);
});

test('segundo envio durante um envio em andamento é recusado', async () => {
  let liberar;
  const { ctrl, view } = montar({ api: { enviarArquivo: () => new Promise((r) => { liberar = r; }) } });
  await ctrl.abrir('nb1');
  const primeiro = ctrl.enviar([arq('a.pdf')]);
  await esperar();
  await ctrl.enviar([arq('b.pdf')]);
  assert.match(view.avisos.at(-1).texto, /Aguarde/);
  liberar();
  await primeiro;
});

test('documento em envio não pode ser excluído e mostra a etapa', async () => {
  let liberar;
  const { ctrl, view, chamadas } = montar({ api: { enviarArquivo: () => new Promise((r) => { liberar = r; }) } });
  await ctrl.abrir('nb1');
  const envio = ctrl.enviar([arq('a.pdf')]);
  await esperar();
  assert.equal(view.ultimo.itens[0].rotulo, 'Enviando…');
  assert.equal(view.ultimo.itens[0].podeExcluir, false);
  await ctrl.excluir('d1');
  assert.equal(chamadas.some((c) => c[0] === 'excluirDocumento'), false);
  liberar();
  await envio;
});

test('excluir remove da lista; erro mantém e avisa; 404 remove', async () => {
  const lista = [doc('a', 'PRONTO'), doc('bb', 'PRONTO')];
  const { ctrl, view } = montar({ lista });
  await ctrl.abrir('nb1');
  await ctrl.excluir('a');
  assert.deepEqual(view.ultimo.itens.map((i) => i.id), ['bb']);

  const falha = montar({ lista, api: { excluirDocumento: async () => { throw new ApiError('Falhou.', { status: 500 }); } } });
  await falha.ctrl.abrir('nb1');
  await falha.ctrl.excluir('a');
  assert.equal(falha.ctrl && falha.view.ultimo.itens.length, 2);
  assert.match(falha.view.avisos.at(-1).texto, /Não foi possível excluir: Falhou/);

  const sumiu = montar({ lista, api: { excluirDocumento: async () => { throw new ApiError('Não existe.', { status: 404 }); } } });
  await sumiu.ctrl.abrir('nb1');
  await sumiu.ctrl.excluir('a');
  assert.equal(sumiu.view.ultimo.itens.length, 1);
});

test('consulta periódica só enquanto há documento em processamento', async () => {
  let estado = 'INDEXANDO';
  const { ctrl, view, agendados, chamadas } = montar({ api: { listarDocumentos: async () => [doc('a', estado)] } });
  await ctrl.abrir('nb1');
  assert.equal(agendados.length, 1);
  assert.equal(agendados[0].ms, 3000);

  await agendados[0].fn();                      // ainda indexando: agenda a próxima, com intervalo maior
  assert.equal(agendados.length, 2);
  assert.equal(agendados[1].ms, 4000);
  estado = 'PRONTO';
  await agendados[1].fn();
  assert.equal(view.ultimo.itens[0].rotulo, 'Pronto');
  assert.equal(agendados.length, 2);            // nada novo agendado: tudo pronto
  assert.equal(chamadas.filter((c) => c[0] === 'listarDocumentos').length, 3);
});

test('o intervalo cresce e a consulta pausa depois de muitas tentativas', async () => {
  const { ctrl, view, agendados } = montar({ api: { listarDocumentos: async () => [doc('a', 'INDEXANDO')] } });
  await ctrl.abrir('nb1');
  for (let i = 0; i < 70 && agendados.length; i += 1) {
    const proximo = agendados.shift();
    await proximo.fn();
  }
  assert.equal(agendados.length, 0);
  assert.match(view.avisos.at(-1).texto, /pausada/);
});

test('aba oculta não consulta, só espera', async () => {
  const { ctrl, agendados, chamadas } = montar({ lista: [doc('a', 'INDEXANDO')], oculta: true });
  await ctrl.abrir('nb1');
  await agendados[0].fn();
  assert.equal(chamadas.filter((c) => c[0] === 'listarDocumentos').length, 1);
  assert.equal(agendados.length, 2);
});

test('resposta atrasada de outro notebook é descartada', async () => {
  let liberar;
  const { ctrl, view } = montar({ api: { listarDocumentos: () => new Promise((r) => { liberar = r; }) } });
  const abrindo = ctrl.abrir('nb1');
  ctrl.fechar();
  liberar([doc('a', 'PRONTO')]);
  await abrindo;
  assert.equal(view.ultimo, null);
  assert.equal(view.visivel, false);
});

// ---------- chamadas da API ----------

function montarApi(fetchFn) {
  const chamadas = [];
  const api = createApi({
    baseUrl: 'https://api.exemplo.com',
    getToken: () => 'tok',
    onSessaoExpirada: () => {},
    fetchFn: async (url, opcoes) => { chamadas.push({ url, ...opcoes }); return fetchFn(url, opcoes); },
  });
  return { api, chamadas };
}
const resp = (status, corpo) => ({ status, ok: status >= 200 && status < 300, json: async () => corpo });

test('API de documentos usa as rotas certas com o token do Cognito', async () => {
  const { api, chamadas } = montarApi(async (url) => {
    if (url.endsWith('/upload')) return resp(201, { documento: doc('d1', 'PENDENTE'), upload: { url: 'https://s3', campos: { key: 'k' } } });
    if (url.endsWith('/confirmar')) return resp(200, { documento: doc('d1', 'INDEXANDO') });
    if (url.endsWith('/documentos')) return resp(200, { documentos: [doc('d1', 'PRONTO'), { lixo: true }] });
    return resp(204);
  });
  assert.equal((await api.listarDocumentos('nb 1')).length, 1);
  assert.equal(chamadas[0].url, 'https://api.exemplo.com/notebooks/nb%201/documentos');
  await api.solicitarUpload('nb1', { nome: 'a.pdf', tamanhoBytes: 10 });
  assert.equal(chamadas[1].url, 'https://api.exemplo.com/notebooks/nb1/documentos/upload');
  assert.deepEqual(JSON.parse(chamadas[1].body), { nome: 'a.pdf', tamanhoBytes: 10 });
  await api.confirmarDocumento('nb1', 'd1');
  assert.equal(chamadas[2].url, 'https://api.exemplo.com/notebooks/nb1/documentos/d1/confirmar');
  await api.excluirDocumento('nb1', 'd1');
  assert.equal(chamadas[3].method, 'DELETE');
  assert.ok(chamadas.every((c) => c.headers.Authorization === 'Bearer tok'));
});

test('envio ao S3: campos assinados primeiro, arquivo por último e SEM token', async () => {
  const { api, chamadas } = montarApi(async () => resp(204));
  const arquivo = new Blob(['conteudo'], { type: 'application/pdf' });
  await api.enviarArquivo({ url: 'https://bucket.s3.amazonaws.com/', campos: { key: 'k', policy: 'p', 'Content-Type': 'application/pdf' } }, arquivo);
  const { url, method, body, headers } = chamadas[0];
  assert.equal(url, 'https://bucket.s3.amazonaws.com/');
  assert.equal(method, 'POST');
  assert.equal(headers, undefined);                       // nada de Authorization para o S3
  assert.deepEqual([...body.keys()], ['key', 'policy', 'Content-Type', 'file']);
});

test('S3 recusa ou rede cai: ApiError com mensagem clara', async () => {
  const campos = { url: 'https://s3', campos: {} };
  const recusa = montarApi(async () => resp(403));
  await assert.rejects(recusa.api.enviarArquivo(campos, new Blob(['x'])), (e) => e instanceof ApiError && e.status === 403 && /recusou/.test(e.message));
  const rede = montarApi(async () => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(rede.api.enviarArquivo(campos, new Blob(['x'])), (e) => e instanceof ApiError && e.retryable && /conexão/.test(e.message));
});

test('resposta inesperada do upload vira ApiError', async () => {
  const { api } = montarApi(async () => resp(201, { documento: doc('d1', 'PENDENTE') }));
  await assert.rejects(api.solicitarUpload('nb1', { nome: 'a.pdf', tamanhoBytes: 1 }), ApiError);
});
