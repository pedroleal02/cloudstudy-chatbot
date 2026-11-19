'use strict';

/*
 * Painel de documentos (DOM). Só desenha e repassa cliques ao controlador (documents.js).
 * Todo texto vindo do servidor entra por textContent, nunca por innerHTML.
 */

// eslint-disable-next-line no-var
var DocumentsView = (function criarView() {
  const $ = (id) => document.getElementById(id);

  const painel = $('docs-panel');
  const resumoEl = $('docs-summary');
  const enviarBtn = $('docs-upload-btn');
  const atualizarBtn = $('docs-refresh-btn');
  const entrada = $('docs-input');
  const nota = $('docs-note');
  const lista = $('docs-list');
  const vazio = $('docs-empty');

  let primeiroDesenho = true;
  let confirmando = null; // { id, timer }: exclusão que espera o segundo clique

  function el(tag, className, texto) {
    const no = document.createElement(tag);
    if (className) no.className = className;
    if (texto !== undefined) no.textContent = texto;
    return no;
  }

  function limparConfirmacao() {
    if (confirmando) clearTimeout(confirmando.timer);
    confirmando = null;
  }

  function aviso(texto, { erro = false, tentar = null, carregando = false } = {}) {
    nota.hidden = !texto;
    nota.dataset.tipo = erro ? 'erro' : 'info';
    nota.dataset.carregando = carregando ? '1' : '';
    nota.replaceChildren();
    if (!texto) return;
    nota.append(document.createTextNode(texto));
    if (tentar) {
      const b = el('button', 'ghost', 'Tentar de novo');
      b.type = 'button';
      b.addEventListener('click', tentar);
      nota.append(b);
    }
  }

  function mostrar() {
    primeiroDesenho = true;
    painel.open = false;
    painel.hidden = false;
  }

  function esconder() {
    limparConfirmacao();
    painel.hidden = true;
    lista.replaceChildren();
    resumoEl.textContent = '';
    entrada.value = '';
    aviso(null);
  }

  function carregando() {
    aviso('Carregando documentos…', { carregando: true });
  }

  function falha(mensagem, tentar) {
    aviso(mensagem, { erro: true, tentar });
  }

  function ocupado(valor) {
    enviarBtn.disabled = valor;
    enviarBtn.textContent = valor ? 'Enviando…' : 'Enviar documentos';
  }

  function linha(item) {
    const li = el('li', 'docs-item');
    li.dataset.tom = item.tom;

    const info = el('div', 'docs-info');
    const nome = el('span', 'docs-nome', item.nome);
    nome.title = item.nome;
    info.append(nome, el('span', 'docs-meta', [String(item.tipo || '').toUpperCase(), item.tamanho].filter(Boolean).join(' · ')));

    const estado = el('span', 'docs-status', item.rotulo);
    estado.dataset.tom = item.tom;

    const excluir = el('button', 'ghost danger', confirmando && confirmando.id === item.id ? 'Confirmar exclusão' : 'Excluir');
    excluir.type = 'button';
    excluir.dataset.id = item.id;
    excluir.disabled = !item.podeExcluir;
    excluir.setAttribute('aria-label', `Excluir ${item.nome}`);

    li.append(info, estado, excluir);
    return li;
  }

  function render(modelo) {
    if (nota.dataset.carregando) aviso(null);
    lista.replaceChildren(...modelo.itens.map(linha));
    vazio.hidden = modelo.itens.length > 0;
    resumoEl.textContent = modelo.resumo;
    if (primeiroDesenho) {
      // abre sozinho quando não há documentos ou algo está sendo processado
      painel.open = modelo.itens.length === 0 || modelo.itens.some((i) => i.tom === 'andamento');
      primeiroDesenho = false;
    }
  }

  function ligar(h) {
    enviarBtn.addEventListener('click', () => entrada.click());
    entrada.addEventListener('change', () => {
      const arquivos = Array.from(entrada.files || []);
      entrada.value = ''; // permite escolher o mesmo arquivo de novo
      h.escolher(arquivos);
    });
    atualizarBtn.addEventListener('click', h.atualizar);

    // exclusão em dois cliques: o primeiro pede confirmação, o segundo (em até 4 s) exclui
    lista.addEventListener('click', (e) => {
      const botao = e.target.closest('button[data-id]');
      if (!botao || botao.disabled) return;
      const id = botao.dataset.id;
      if (confirmando && confirmando.id === id) {
        limparConfirmacao();
        h.excluir(id);
        return;
      }
      limparConfirmacao();
      botao.textContent = 'Confirmar exclusão';
      confirmando = {
        id,
        timer: setTimeout(() => {
          confirmando = null;
          // a lista pode ter sido redesenhada (consulta periódica): procura o botão atual
          const atual = lista.querySelector(`button[data-id="${CSS.escape(id)}"]`);
          if (atual) atual.textContent = 'Excluir';
        }, 4000),
      };
    });
  }

  return { ligar, mostrar, esconder, carregando, falha, aviso, ocupado, render };
})();
