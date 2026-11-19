'use strict';

/*
 * Controlador dos documentos de um notebook (commit 10). Sem DOM: a tela entra por `view`
 * e a API por `api`, então dá para testar em Node.
 *
 * Fluxo de um envio: registra (PENDENTE) -> POST direto ao S3 -> confirma (ENVIADO/INDEXANDO).
 * Depois a Knowledge Base indexa e o estado vira PRONTO ou ERRO. Enquanto houver documento
 * em processamento, a lista é consultada de novo, com intervalo crescente.
 */

const TIPOS_ACEITOS = ['pdf', 'md', 'txt'];
const MAX_BYTES = 50 * 1024 * 1024; // mesmo limite do backend
const MAX_NOME = 255;

const ROTULOS_STATUS = {
  PENDENTE: 'Envio incompleto',
  ENVIADO: 'Aguardando indexação',
  INDEXANDO: 'Indexando…',
  PRONTO: 'Pronto',
  ERRO: 'Erro ao indexar',
};
const TOM_STATUS = { PENDENTE: 'aviso', ENVIADO: 'andamento', INDEXANDO: 'andamento', PRONTO: 'ok', ERRO: 'erro' };
const ROTULOS_ETAPA = { registrando: 'Preparando envio…', enviando: 'Enviando…', confirmando: 'Confirmando…' };

function extensaoDe(nome) {
  const partes = String(nome || '').split('.');
  return partes.length > 1 ? partes.pop().toLowerCase() : '';
}

/** Devolve o motivo da recusa (texto) ou null se o arquivo pode ser enviado. */
function validarArquivo(arquivo) {
  const nome = arquivo && typeof arquivo.name === 'string' ? arquivo.name : '';
  if (!nome) return 'nome inválido.';
  if (nome.length > MAX_NOME) return `o nome é longo demais (máx. ${MAX_NOME} caracteres).`;
  if (!TIPOS_ACEITOS.includes(extensaoDe(nome))) return 'tipo não aceito. Use PDF, Markdown (.md) ou texto (.txt).';
  if (!(arquivo.size > 0)) return 'o arquivo está vazio.';
  if (arquivo.size > MAX_BYTES) return 'o arquivo passa de 50 MB.';
  return null;
}

function formatarTamanho(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1).replace('.', ',')} KB`;
  return `${(n / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
}

function plural(n, singular, pluralTxt) {
  return `${n} ${n === 1 ? singular : pluralTxt}`;
}

function resumir(itens) {
  if (!itens.length) return '';
  const prontos = itens.filter((i) => i.tom === 'ok').length;
  const andamento = itens.filter((i) => i.tom === 'andamento').length;
  const problemas = itens.filter((i) => i.tom === 'erro' || i.tom === 'aviso').length;
  const partes = [plural(itens.length, 'documento', 'documentos')];
  if (prontos) partes.push(plural(prontos, 'pronto', 'prontos'));
  if (andamento) partes.push(`${andamento} em processamento`);
  if (problemas) partes.push(plural(problemas, 'com problema', 'com problema'));
  return partes.join(' · ');
}

function createDocuments(env) {
  const { api, view } = env;
  const agendar = env.setTimeoutFn || ((fn, ms) => setTimeout(fn, ms));
  const cancelar = env.clearTimeoutFn || ((t) => clearTimeout(t));
  const oculta = env.isHidden || (() => typeof document !== 'undefined' && document.hidden);

  const ATRASOS = [3000, 4000, 6000, 8000, 10000, 15000]; // ms entre consultas
  const MAX_TENTATIVAS = 60; // depois disso a atualização automática pausa

  let notebookId = null;
  let geracao = 0; // muda ao abrir/fechar: respostas atrasadas de outro notebook são descartadas
  let docs = [];
  const locais = new Map(); // envios em andamento neste navegador (chave: documentoId ou id provisório)
  const excluindo = new Set();
  let timer = null;
  let tentativas = 0;
  let enviando = false;
  let seq = 0;

  const ordenar = (lista) => [...lista].sort((a, b) => String(a.criadoEm).localeCompare(String(b.criadoEm)));
  const textoDoErro = (err) => (err && err.name === 'ApiError' && err.message ? err.message : 'erro inesperado.');

  function itemDoServidor(d) {
    const local = locais.get(d.documentoId);
    const base = { id: d.documentoId, nome: d.nome, tipo: d.tipo || extensaoDe(d.nome), tamanho: formatarTamanho(d.tamanhoBytes) };
    if (local) return { ...base, rotulo: ROTULOS_ETAPA[local.etapa], tom: 'andamento', podeExcluir: false };
    return {
      ...base,
      rotulo: ROTULOS_STATUS[d.status] || d.status,
      tom: TOM_STATUS[d.status] || 'aviso',
      podeExcluir: !excluindo.has(d.documentoId),
    };
  }

  function modelo() {
    const itens = docs.map(itemDoServidor);
    for (const l of locais.values()) {
      if (l.documentoId) continue; // já aparece pela lista do servidor
      itens.push({
        id: l.chave, nome: l.nome, tipo: extensaoDe(l.nome), tamanho: formatarTamanho(l.tamanhoBytes),
        rotulo: ROTULOS_ETAPA[l.etapa], tom: 'andamento', podeExcluir: false,
      });
    }
    return { itens, resumo: resumir(itens) };
  }

  const desenhar = () => view.render(modelo());
  const temPendentes = () => docs.some((d) => d.status === 'ENVIADO' || d.status === 'INDEXANDO');

  function pararTimer() {
    if (timer !== null) cancelar(timer);
    timer = null;
  }

  function agendarConsulta() {
    pararTimer();
    if (!notebookId || !temPendentes()) return;
    if (tentativas >= MAX_TENTATIVAS) {
      view.aviso('A atualização automática foi pausada. Use "Atualizar" para ver o estado mais recente.');
      return;
    }
    timer = agendar(async () => {
      timer = null;
      if (oculta()) { // aba em segundo plano: não consulta, só espera
        agendarConsulta();
        return;
      }
      tentativas += 1;
      await carregar({ silencioso: true });
    }, ATRASOS[Math.min(tentativas, ATRASOS.length - 1)]);
  }

  async function carregar({ silencioso = false } = {}) {
    const minha = geracao;
    if (!silencioso) view.carregando();
    try {
      const lista = await api.listarDocumentos(notebookId);
      if (minha !== geracao) return;
      const antes = new Map(docs.map((d) => [d.documentoId, d.status]));
      docs = ordenar(lista);
      if (docs.some((d) => antes.has(d.documentoId) && antes.get(d.documentoId) !== d.status)) tentativas = 0;
      desenhar();
      agendarConsulta();
    } catch (err) {
      if (minha !== geracao) return;
      if (silencioso) agendarConsulta(); // falha passageira: tenta de novo depois
      else view.falha(`Não foi possível carregar os documentos: ${textoDoErro(err)}`, () => carregar());
    }
  }

  function abrir(id) {
    fechar();
    notebookId = id;
    view.mostrar();
    return carregar();
  }

  function fechar() {
    geracao += 1;
    pararTimer();
    notebookId = null;
    docs = [];
    locais.clear();
    excluindo.clear();
    tentativas = 0;
    enviando = false;
    view.ocupado(false);
    view.esconder();
  }

  function atualizar() {
    if (!notebookId) return Promise.resolve();
    tentativas = 0;
    return carregar();
  }

  async function enviarUm(arquivo, minha, problemas) {
    const motivo = validarArquivo(arquivo);
    if (motivo) {
      problemas.push(`${(arquivo && arquivo.name) || 'Arquivo'}: ${motivo}`);
      return;
    }
    const nb = notebookId;
    const local = { chave: `local-${(seq += 1)}`, nome: arquivo.name, tamanhoBytes: arquivo.size, etapa: 'registrando', documentoId: null };
    locais.set(local.chave, local);
    desenhar();

    let registrado = null;
    try {
      const { documento, upload } = await api.solicitarUpload(nb, { nome: arquivo.name, tamanhoBytes: arquivo.size });
      if (minha !== geracao) return;
      registrado = documento;
      local.documentoId = documento.documentoId;
      local.etapa = 'enviando';
      locais.set(documento.documentoId, local);
      docs = ordenar([...docs, documento]);
      desenhar();

      await api.enviarArquivo(upload, arquivo);
      if (minha !== geracao) return;
      local.etapa = 'confirmando';
      desenhar();

      const confirmado = await api.confirmarDocumento(nb, documento.documentoId);
      if (minha !== geracao) return;
      docs = ordenar(docs.map((d) => (d.documentoId === confirmado.documentoId ? confirmado : d)));
    } catch (err) {
      if (minha !== geracao) return;
      problemas.push(`${arquivo.name}: ${textoDoErro(err)}`);
      if (registrado) {
        // não deixa registro órfão: o backend também apaga o arquivo, se ele chegou a subir
        try { await api.excluirDocumento(nb, registrado.documentoId); } catch { /* sobra como "Envio incompleto", e dá para excluir */ }
        if (minha === geracao) docs = docs.filter((d) => d.documentoId !== registrado.documentoId);
      }
    } finally {
      if (minha === geracao) {
        locais.delete(local.chave);
        if (registrado) locais.delete(registrado.documentoId);
        desenhar();
      }
    }
  }

  async function enviar(arquivos) {
    const lista = Array.from(arquivos || []);
    if (!notebookId || !lista.length) return;
    if (enviando) {
      view.aviso('Aguarde o envio atual terminar.');
      return;
    }
    const minha = geracao;
    enviando = true;
    view.ocupado(true);
    view.aviso(null);
    const problemas = [];
    try {
      for (const arquivo of lista) {
        if (minha !== geracao) return;
        await enviarUm(arquivo, minha, problemas);
      }
    } finally {
      if (minha === geracao) {
        enviando = false;
        view.ocupado(false);
      }
    }
    if (minha !== geracao) return;
    tentativas = 0;
    await carregar({ silencioso: true }); // sincroniza com o servidor e liga a consulta periódica
    if (minha === geracao && problemas.length) view.aviso(problemas.join('\n'), { erro: true });
  }

  async function excluir(id) {
    const minha = geracao;
    const nb = notebookId;
    if (!nb || excluindo.has(id) || locais.has(id)) return;
    excluindo.add(id);
    desenhar();
    try {
      await api.excluirDocumento(nb, id);
      if (minha !== geracao) return;
      docs = docs.filter((d) => d.documentoId !== id);
      view.aviso(null);
    } catch (err) {
      if (minha !== geracao) return;
      if (err && err.status === 404) docs = docs.filter((d) => d.documentoId !== id); // já não existia
      else view.aviso(`Não foi possível excluir: ${textoDoErro(err)}`, { erro: true });
    } finally {
      if (minha === geracao) {
        excluindo.delete(id);
        desenhar();
        agendarConsulta();
      }
    }
  }

  return { abrir, fechar, atualizar, enviar, excluir };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createDocuments, validarArquivo, formatarTamanho, resumir }; // testes em Node
}
