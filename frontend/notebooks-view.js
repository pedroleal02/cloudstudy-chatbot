'use strict';

/*
 * Tela dos notebooks (DOM). Só desenha e repassa cliques ao controlador
 * (notebooks.js). Todo texto vindo do servidor entra por textContent, nunca
 * por innerHTML.
 */

// eslint-disable-next-line no-var
var NotebooksView = (function criarView() {
  const $ = (id) => document.getElementById(id);

  const tela = $('notebooks-screen');
  const tituloLista = $('notebooks-title');
  const status = $('notebooks-status');
  const grade = $('notebooks-list');
  const vazio = $('notebooks-empty');
  const novoBtn = $('new-notebook-btn');

  const barra = $('notebook-bar');
  const voltarBtn = $('back-btn');
  const nomeEl = $('notebook-name');
  const descEl = $('notebook-desc');
  const editarBtn = $('edit-notebook-btn');
  const excluirBtn = $('delete-notebook-btn');

  const formDialog = $('notebook-dialog');
  const formTitulo = $('notebook-dialog-title');
  const form = $('notebook-form');
  const campoNome = $('notebook-name-input');
  const campoDesc = $('notebook-desc-input');
  const formErro = $('notebook-form-error');
  const salvarBtn = $('notebook-save-btn');
  const cancelarBtn = $('notebook-cancel-btn');

  const delDialog = $('delete-dialog');
  const delTexto = $('delete-text');
  const delErro = $('delete-error');
  const delConfirmar = $('delete-confirm-btn');
  const delCancelar = $('delete-cancel-btn');

  const formatoData = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'short', year: 'numeric' });

  let aoSalvar = null;
  let aoConfirmar = null;
  let ocupado = false;
  let rotuloSalvar = 'Salvar';

  function el(tag, className, texto) {
    const no = document.createElement(tag);
    if (className) no.className = className;
    if (texto !== undefined) no.textContent = texto;
    return no;
  }

  function dataLegivel(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : formatoData.format(d);
  }

  // só mensagens que escrevemos (ApiError e ErroDeFormulario) vão para a tela
  function mensagemDe(err, padrao) {
    const conhecido = err && (err.name === 'ApiError' || err.name === 'ErroDeFormulario');
    return conhecido && err.message ? err.message : padrao;
  }

  function mostrarStatus(texto, { erro = false, tentar = null } = {}) {
    status.replaceChildren();
    status.classList.toggle('is-error', erro);
    if (!texto) {
      status.hidden = true;
      return;
    }
    status.appendChild(el('span', '', texto));
    if (tentar) {
      const botao = el('button', 'retry', 'Tentar de novo');
      botao.type = 'button';
      botao.addEventListener('click', tentar);
      status.appendChild(botao);
    }
    status.hidden = false;
  }

  // ---------- lista ----------

  function cartao(notebook) {
    const item = el('li');
    const botao = el('button', 'nb-card');
    botao.type = 'button';
    botao.dataset.id = notebook.notebookId;
    botao.appendChild(el('span', 'nb-card-name', notebook.nome));
    if (notebook.descricao) botao.appendChild(el('span', 'nb-card-desc', notebook.descricao));
    const data = dataLegivel(notebook.criadoEm);
    if (data) botao.appendChild(el('span', 'nb-card-meta', `Criado em ${data}`));
    item.appendChild(botao);
    return item;
  }

  function mostrarLista(lista) {
    barra.hidden = true;
    tela.hidden = false;
    mostrarStatus('');
    grade.replaceChildren(...lista.map(cartao));
    grade.hidden = lista.length === 0;
    vazio.hidden = lista.length !== 0;
    novoBtn.hidden = false;
    window.scrollTo(0, 0);
    tituloLista.focus({ preventScroll: true });
  }

  function listaCarregando() {
    barra.hidden = true;
    tela.hidden = false;
    grade.replaceChildren();
    grade.hidden = true;
    vazio.hidden = true;
    novoBtn.hidden = true;
    mostrarStatus('Carregando seus notebooks…');
  }

  function listaErro(mensagem, tentar) {
    listaCarregando();
    mostrarStatus(mensagem, { erro: true, tentar });
  }

  function aviso(texto) {
    mostrarStatus(texto);
  }

  // ao fechar um diálogo o navegador devolve o foco ao botão que o abriu, que pode ter
  // sumido da tela (ex.: depois de criar, a lista dá lugar ao notebook): foca o título atual
  function focarTelaAtual() {
    (barra.hidden ? tituloLista : nomeEl).focus({ preventScroll: true });
  }

  // ---------- notebook aberto ----------

  function atualizarNotebook(notebook) {
    nomeEl.textContent = notebook.nome;
    descEl.textContent = notebook.descricao || '';
    descEl.hidden = !notebook.descricao;
  }

  function mostrarNotebook(notebook) {
    tela.hidden = true;
    barra.hidden = false;
    atualizarNotebook(notebook);
    window.scrollTo(0, 0);
    nomeEl.focus({ preventScroll: true });
  }

  function bloquear(valor) {
    voltarBtn.disabled = valor;
    editarBtn.disabled = valor;
    excluirBtn.disabled = valor;
  }

  // ---------- formulário (criar e editar) ----------

  function definirOcupadoForm(valor) {
    ocupado = valor;
    salvarBtn.disabled = valor;
    cancelarBtn.disabled = valor;
    salvarBtn.textContent = valor ? 'Salvando…' : rotuloSalvar;
  }

  function abrirFormulario({ titulo, rotulo, nome, descricao, aoSalvar: salvar }) {
    aoSalvar = salvar;
    rotuloSalvar = rotulo;
    formTitulo.textContent = titulo;
    campoNome.value = nome;
    campoDesc.value = descricao;
    formErro.hidden = true;
    definirOcupadoForm(false);
    formDialog.showModal();
    campoNome.focus();
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (ocupado || !aoSalvar) return;
    formErro.hidden = true;
    definirOcupadoForm(true);
    try {
      await aoSalvar({ nome: campoNome.value, descricao: campoDesc.value });
      if (formDialog.open) formDialog.close();
      focarTelaAtual();
    } catch (err) {
      if (formDialog.open) {
        formErro.textContent = mensagemDe(err, 'Algo deu errado. Tente novamente.');
        formErro.hidden = false;
        campoNome.focus();
      }
    } finally {
      definirOcupadoForm(false);
    }
  });

  cancelarBtn.addEventListener('click', () => formDialog.close());
  // Esc não fecha enquanto salva
  formDialog.addEventListener('cancel', (e) => { if (ocupado) e.preventDefault(); });
  formDialog.addEventListener('close', () => { aoSalvar = null; });

  // ---------- confirmação de exclusão ----------

  function definirOcupadoDel(valor) {
    ocupado = valor;
    delConfirmar.disabled = valor;
    delCancelar.disabled = valor;
    delConfirmar.textContent = valor ? 'Excluindo…' : 'Excluir';
  }

  function confirmarExclusao({ nome, aoConfirmar: confirmar }) {
    aoConfirmar = confirmar;
    delTexto.textContent =
      `Você vai excluir “${nome}”. Tudo o que estiver dentro dele (documentos e conversas) ` +
      'também será apagado, e isso não pode ser desfeito.';
    delErro.hidden = true;
    definirOcupadoDel(false);
    delDialog.showModal();
    delCancelar.focus(); // o foco começa no botão seguro
  }

  delConfirmar.addEventListener('click', async () => {
    if (ocupado || !aoConfirmar) return;
    delErro.hidden = true;
    definirOcupadoDel(true);
    try {
      await aoConfirmar();
      if (delDialog.open) delDialog.close();
      focarTelaAtual();
    } catch (err) {
      if (delDialog.open) {
        delErro.textContent = mensagemDe(err, 'Não foi possível excluir. Tente novamente.');
        delErro.hidden = false;
      }
    } finally {
      definirOcupadoDel(false);
    }
  });

  delCancelar.addEventListener('click', () => delDialog.close());
  delDialog.addEventListener('cancel', (e) => { if (ocupado) e.preventDefault(); });
  delDialog.addEventListener('close', () => { aoConfirmar = null; });

  // ---------- geral ----------

  function fecharTudo() {
    tela.hidden = true;
    barra.hidden = true;
    grade.replaceChildren();
    mostrarStatus('');
    ocupado = false;
    if (formDialog.open) formDialog.close();
    if (delDialog.open) delDialog.close();
  }

  function ligar(h) {
    novoBtn.addEventListener('click', h.novo);
    voltarBtn.addEventListener('click', h.voltar);
    editarBtn.addEventListener('click', h.editar);
    excluirBtn.addEventListener('click', h.excluir);
    grade.addEventListener('click', (e) => {
      const botao = e.target.closest('button[data-id]');
      if (botao) h.abrir(botao.dataset.id);
    });
  }

  return {
    ligar,
    listaCarregando,
    listaErro,
    mostrarLista,
    mostrarNotebook,
    atualizarNotebook,
    aviso,
    abrirFormulario,
    confirmarExclusao,
    bloquear,
    fecharTudo,
  };
}());