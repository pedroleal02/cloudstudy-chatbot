'use strict';

/*
 * Controlador dos notebooks: lista, criação, edição, exclusão e seleção.
 *
 * Não mexe no DOM. Quem desenha a tela é a `view` (notebooks-view.js) e quem
 * fala com o servidor é a `api` (api.js); os dois entram por parâmetro, então
 * este arquivo roda em Node nos testes.
 *
 * O notebook aberto fica guardado no sessionStorage, por usuário, para voltar
 * a ele ao recarregar a página.
 */

const MAX_NOME_NOTEBOOK = 80;
const MAX_DESCRICAO_NOTEBOOK = 500;

class ErroDeFormulario extends Error {
  constructor(message) {
    super(message);
    this.name = 'ErroDeFormulario';
  }
}

// mesmas regras do backend (modelos.py); o servidor valida de novo
function normalizarCampos({ nome, descricao }) {
  const limpo = (t) => String(t ?? '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim();
  const dados = { nome: limpo(nome), descricao: limpo(descricao) };

  if (!dados.nome) throw new ErroDeFormulario('Dê um nome ao notebook.');
  if (dados.nome.length > MAX_NOME_NOTEBOOK) {
    throw new ErroDeFormulario(`O nome pode ter até ${MAX_NOME_NOTEBOOK} caracteres.`);
  }
  if (dados.descricao.length > MAX_DESCRICAO_NOTEBOOK) {
    throw new ErroDeFormulario(`A descrição pode ter até ${MAX_DESCRICAO_NOTEBOOK} caracteres.`);
  }
  return dados;
}

function createNotebooks(env) {
  const { api, storage, view, onAbrir, onFechar } = env;

  let chave = null; // chave do sessionStorage (uma por usuário)
  let lista = [];
  let atual = null;
  let bloqueado = false;
  let versao = 0; // muda ao reiniciar: respostas antigas são descartadas

  // ---------- armazenamento ----------

  function lembrar(id) {
    try {
      if (id) storage.setItem(chave, id);
      else storage.removeItem(chave);
    } catch { /* storage indisponível */ }
  }

  function lembrado() {
    try { return storage.getItem(chave); } catch { return null; }
  }

  // ---------- navegação ----------

  function mostrarLista() {
    atual = null;
    lembrar(null);
    view.mostrarLista(lista);
    onFechar();
  }

  function substituir(notebook) {
    lista = lista.map((n) => (n.notebookId === notebook.notebookId ? notebook : n));
    if (atual && atual.notebookId === notebook.notebookId) atual = notebook;
  }

  async function carregar(minha, restaurar) {
    view.listaCarregando();

    let itens;
    try {
      itens = await api.listarNotebooks();
    } catch (err) {
      if (minha !== versao) return;
      view.listaErro(err.message, () => carregar(versao, restaurar));
      return;
    }
    if (minha !== versao) return;

    lista = itens;
    const id = restaurar ? lembrado() : null;
    if (id && lista.some((n) => n.notebookId === id)) abrir(id);
    else mostrarLista();
  }

  /** Chamar depois do login, com o `sub` do usuário. */
  function iniciar(usuarioId) {
    const minha = ++versao;
    chave = `cloudstudy.notebook.${usuarioId}`;
    lista = [];
    atual = null;
    return carregar(minha, true);
  }

  /** Chamar ao sair ou quando a sessão expira: esquece tudo e fecha a tela. */
  function reset() {
    versao += 1;
    lista = [];
    atual = null;
    view.fecharTudo();
  }

  async function abrir(id) {
    const notebook = lista.find((n) => n.notebookId === id);
    if (!notebook) return;

    const minha = versao;
    atual = notebook;
    lembrar(id);
    view.mostrarNotebook(notebook);
    onAbrir(notebook);

    // mostra já o que está na lista e confere no servidor em seguida
    const aindaAberto = () => minha === versao && atual && atual.notebookId === id;
    try {
      const fresco = await api.obterNotebook(id);
      if (!aindaAberto()) return;
      substituir(fresco);
      view.atualizarNotebook(fresco);
    } catch (err) {
      if (!aindaAberto()) return;
      if (err.status === 404) {
        lista = lista.filter((n) => n.notebookId !== id);
        mostrarLista();
        view.aviso('Esse notebook não existe mais.');
      }
      // outras falhas: segue com os dados que já estão na tela
    }
  }

  function voltar() {
    if (bloqueado) return;
    mostrarLista();
  }

  // ---------- criar, editar, excluir ----------

  function novo() {
    if (bloqueado) return;
    view.abrirFormulario({
      titulo: 'Novo notebook',
      rotulo: 'Criar notebook',
      nome: '',
      descricao: '',
      aoSalvar: async (valores) => {
        const dados = normalizarCampos(valores);
        const notebook = await api.criarNotebook(dados);
        lista.push(notebook);
        abrir(notebook.notebookId); // já entra no notebook recém-criado
      },
    });
  }

  function editar() {
    if (bloqueado || !atual) return;
    const id = atual.notebookId;
    view.abrirFormulario({
      titulo: 'Editar notebook',
      rotulo: 'Salvar',
      nome: atual.nome,
      descricao: atual.descricao || '',
      aoSalvar: async (valores) => {
        const dados = normalizarCampos(valores);
        if (atual && dados.nome === atual.nome && dados.descricao === (atual.descricao || '')) return;

        let notebook;
        try {
          notebook = await api.atualizarNotebook(id, dados);
        } catch (err) {
          if (err.status !== 404) throw err;
          // foi excluído em outro lugar
          lista = lista.filter((n) => n.notebookId !== id);
          mostrarLista();
          view.aviso('Esse notebook não existe mais.');
          return;
        }
        substituir(notebook);
        if (atual && atual.notebookId === id) view.atualizarNotebook(notebook);
      },
    });
  }

  function excluir() {
    if (bloqueado || !atual) return;
    const alvo = atual;
    view.confirmarExclusao({
      nome: alvo.nome,
      aoConfirmar: async () => {
        try {
          await api.excluirNotebook(alvo.notebookId);
        } catch (err) {
          if (err.status !== 404) throw err; // já não existia: o resultado é o mesmo
        }
        lista = lista.filter((n) => n.notebookId !== alvo.notebookId);
        if (atual && atual.notebookId === alvo.notebookId) mostrarLista();
        view.aviso(`Notebook "${alvo.nome}" excluído.`);
      },
    });
  }

  /** Enquanto uma pergunta está em andamento não se troca nem se altera o notebook. */
  function bloquear(valor) {
    bloqueado = Boolean(valor);
    view.bloquear(bloqueado);
  }

  view.ligar({ novo, voltar, editar, excluir, abrir });

  return { iniciar, reset, bloquear, abrir, voltar, novo, editar, excluir };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createNotebooks, normalizarCampos, ErroDeFormulario }; // testes em Node
}