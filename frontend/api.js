'use strict';

/*
 * Cliente da API do CloudStudy. Toda chamada leva o ID token do Cognito
 * (Authorization: Bearer ...). Se não há token, ou o API Gateway devolve 401,
 * chama `onSessaoExpirada` (que mostra a tela de entrada) e falha com ApiError.
 *
 * Sem DOM: fetch, token e relógio entram por parâmetro, então dá para testar em Node.
 */

class ApiError extends Error {
  constructor(message, { status = 0, retryable = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryable = retryable;
  }
}

function createApi(env) {
  const { getToken, onSessaoExpirada, fetchFn, timeoutMs = 35000 } = env;
  // sem barra no final
  const base = String(env.baseUrl || '').replace(/\/+$/, '');
  const MSG_SESSAO_EXPIRADA = 'Sua sessão expirou. Entre de novo para continuar.';

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

  function sessaoExpirada() {
    if (typeof onSessaoExpirada === 'function') onSessaoExpirada();
    return new ApiError(MSG_SESSAO_EXPIRADA, { status: 401 });
  }

  /** Devolve o JSON da resposta ({} em 204). Lança ApiError em qualquer falha. */
  async function request(method, caminho, corpo) {
    if (!base) {
      throw new ApiError('A URL da API não está configurada. Verifique o config.js.');
    }

    // o auth.js não renova token: quando expira, o usuário volta para o login
    const token = getToken();
    if (!token) throw sessaoExpirada();

    const headers = { Authorization: `Bearer ${token}` };
    const opcoes = { method, headers };
    if (corpo !== undefined) {
      headers['Content-Type'] = 'application/json';
      opcoes.body = JSON.stringify(corpo);
    }

    const controller = new AbortController();
    opcoes.signal = controller.signal;
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetchFn(`${base}${caminho}`, opcoes);

      if (response.status === 401) {
        // o API Gateway recusou o token (expirado, revogado ou de outro pool)
        throw sessaoExpirada();
      }
      if (response.status === 204) return {};

      const data = await lerJson(response);
      if (!response.ok) throw erroDaApi(response.status, data);
      return data;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (err && err.name === 'AbortError') {
        throw new ApiError('A resposta demorou demais. Tente novamente.', { retryable: true });
      }
      // falha de rede ou CORS
      throw new ApiError('Não foi possível conectar ao servidor. Verifique sua conexão e tente de novo.', { retryable: true });
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function inesperada(status) {
    return new ApiError('A API devolveu uma resposta inesperada.', { status, retryable: true });
  }

  const pathNotebook = (id) => `/notebooks/${encodeURIComponent(id)}`;
  const notebookValido = (n) => Boolean(n) && typeof n.notebookId === 'string' && typeof n.nome === 'string';

  // ---------- chat ----------

  /** POST /chat -> { resposta, referencias }. A busca é feita só nos documentos deste notebook. */
  async function chat(pergunta, notebookId) {
    if (typeof notebookId !== 'string' || !notebookId) {
      throw new ApiError('Abra um notebook para fazer perguntas.');
    }
    const data = await request('POST', '/chat', { pergunta, notebookId });
    if (typeof data.resposta !== 'string' || !data.resposta.trim()) {
      throw new ApiError('A API não devolveu uma resposta.', { retryable: true });
    }
    return data;
  }

  // ---------- notebooks ----------

  async function listarNotebooks() {
    const data = await request('GET', '/notebooks');
    if (!Array.isArray(data.notebooks)) throw inesperada(200);
    return data.notebooks.filter(notebookValido);
  }

  async function obterNotebook(id) {
    const data = await request('GET', pathNotebook(id));
    if (!notebookValido(data.notebook)) throw inesperada(200);
    return data.notebook;
  }

  async function criarNotebook({ nome, descricao }) {
    const data = await request('POST', '/notebooks', { nome, descricao });
    if (!notebookValido(data.notebook)) throw inesperada(201);
    return data.notebook;
  }

  async function atualizarNotebook(id, campos) {
    const data = await request('PATCH', pathNotebook(id), campos);
    if (!notebookValido(data.notebook)) throw inesperada(200);
    return data.notebook;
  }

  async function excluirNotebook(id) {
    await request('DELETE', pathNotebook(id));
  }

  return {
    MSG_SESSAO_EXPIRADA,
    chat,
    listarNotebooks,
    obterNotebook,
    criarNotebook,
    atualizarNotebook,
    excluirNotebook,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createApi, ApiError }; // testes em Node
} else {
  // eslint-disable-next-line no-var
  var Api = createApi({
    baseUrl: typeof API_URL === 'string' ? API_URL : '',
    getToken: () => Auth.getIdToken(),
    onSessaoExpirada: () => showLogin(Api.MSG_SESSAO_EXPIRADA), // showLogin vem do script.js
    fetchFn: (...args) => window.fetch(...args),
  });
}
