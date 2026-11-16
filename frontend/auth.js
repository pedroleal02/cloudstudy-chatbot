'use strict';

/*
 * Autenticação com Amazon Cognito (login hospedado) usando OAuth 2.0
 * Authorization Code + PKCE. Sem bibliotecas, sem build e sem segredo no
 * navegador: o cliente do Cognito é "público" e o código de verificação (PKCE)
 * faz o papel do segredo.
 *
 * A senha NUNCA passa por este código: quem coleta e valida é a página do Cognito.
 *
 * Os tokens ficam em sessionStorage (somem ao fechar a aba). Ao expirar, o
 * usuário volta para a tela de entrada; como a página do Cognito mantém a
 * sessão dele, entrar de novo é quase instantâneo.
 *
 * Neste commit os tokens só identificam quem está usando a interface.
 * A API ainda NÃO exige token: isso é o commit 3 (Cognito authorizer).
 */

function createAuth(env) {
  const { config, storage, location, history, fetchFn, cryptoObj, now } = env;

  const KEY_TOKENS = 'cloudstudy.tokens';
  const KEY_PKCE = 'cloudstudy.pkce';
  const SCOPES = 'openid email';

  // ---------- utilidades ----------

  function base64url(bytes) {
    let bin = '';
    bytes.forEach((b) => { bin += String.fromCharCode(b); });
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function randomString(nBytes) {
    return base64url(cryptoObj.getRandomValues(new Uint8Array(nBytes)));
  }

  async function challengeOf(verifier) {
    const digest = await cryptoObj.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
    return base64url(new Uint8Array(digest));
  }

  function decodePayload(jwt) {
    try {
      const parte = jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const bin = atob(parte.padEnd(Math.ceil(parte.length / 4) * 4, '='));
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch {
      return null;
    }
  }

  function guardar(chave, valor) {
    try { storage.setItem(chave, JSON.stringify(valor)); } catch { /* storage indisponível */ }
  }

  function ler(chave) {
    try { return JSON.parse(storage.getItem(chave)); } catch { return null; }
  }

  function apagar(chave) {
    try { storage.removeItem(chave); } catch { /* ignora */ }
  }

  // ---------- configuração ----------

  function dominio() {
    return String(config.domain || '').replace(/\/+$/, '');
  }

  function configurado() {
    return Boolean(dominio() && config.clientId && config.region && config.userPoolId);
  }

  // precisa ser IGUAL a uma "Allowed callback URL" do app client
  function redirectUri() {
    return location.origin + location.pathname.replace(/index\.html$/, '');
  }

  function emissor() {
    return `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`;
  }

  // ---------- fluxo de login ----------

  async function login() {
    if (!configurado()) {
      throw new Error('O login não está configurado. Verifique COGNITO_CONFIG no config.js.');
    }
    const verifier = randomString(32);
    const state = randomString(16);
    guardar(KEY_PKCE, { verifier, state });

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: config.clientId,
      redirect_uri: redirectUri(),
      scope: SCOPES,
      state,
      code_challenge: await challengeOf(verifier),
      code_challenge_method: 'S256',
    });
    location.assign(`${dominio()}/oauth2/authorize?${params}`);
  }

  async function trocarCodigo(code, verifier) {
    let resp;
    try {
      resp = await fetchFn(`${dominio()}/oauth2/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: config.clientId,
          code,
          redirect_uri: redirectUri(),
          code_verifier: verifier,
        }),
      });
    } catch {
      throw new Error('Não foi possível falar com o serviço de login. Tente de novo.');
    }
    const dados = await resp.json().catch(() => ({}));
    if (!resp.ok || !dados.id_token || !dados.access_token) {
      throw new Error('Não foi possível concluir o login. Tente entrar de novo.');
    }
    return dados;
  }

  /**
   * Chamar uma vez ao carregar a página. Se a URL traz o retorno do Cognito
   * (?code=... ou ?error=...), conclui o login e limpa a URL.
   * Devolve { status: 'nenhum' | 'ok' | 'erro', mensagem? }.
   */
  async function tratarRetorno() {
    const q = new URLSearchParams(location.search);
    const temRetorno = q.has('code') || q.has('error');
    if (!temRetorno) return { status: 'nenhum' };

    const pkce = ler(KEY_PKCE);
    apagar(KEY_PKCE); // uso único
    history.replaceState(null, '', redirectUri()); // não deixa code/state na barra nem no histórico

    if (q.has('error')) {
      return { status: 'erro', mensagem: 'O login foi cancelado ou recusado.' };
    }
    if (!pkce || !q.get('state') || q.get('state') !== pkce.state) {
      return { status: 'erro', mensagem: 'Resposta de login inválida. Tente entrar de novo.' };
    }

    try {
      const t = await trocarCodigo(q.get('code'), pkce.verifier);
      guardar(KEY_TOKENS, { idToken: t.id_token, accessToken: t.access_token });
    } catch (e) {
      return { status: 'erro', mensagem: e.message };
    }
    return getUser() ? { status: 'ok' } : { status: 'erro', mensagem: 'O token recebido não é válido.' };
  }

  // ---------- sessão ----------

  function tokensValidos() {
    const t = ler(KEY_TOKENS);
    if (!t || typeof t.idToken !== 'string') return null;
    const p = decodePayload(t.idToken);
    if (!p) return null;

    const aud = p.aud === config.clientId;
    const iss = p.iss === emissor();
    const tipo = p.token_use === 'id';
    const vivo = typeof p.exp === 'number' && p.exp * 1000 > now();
    return aud && iss && tipo && vivo ? { tokens: t, payload: p } : null;
  }

  /** { sub, email } do usuário logado, ou null (sem sessão ou token expirado). */
  function getUser() {
    const v = tokensValidos();
    if (!v) return null;
    return { sub: v.payload.sub, email: v.payload.email || '' };
  }

  /** Para o commit 3 (Authorization: Bearer ...). Ainda não é usado pela API. */
  function getIdToken() {
    const v = tokensValidos();
    return v ? v.tokens.idToken : null;
  }

  function logout() {
    apagar(KEY_TOKENS);
    apagar(KEY_PKCE);
    if (!configurado()) return;
    const params = new URLSearchParams({ client_id: config.clientId, logout_uri: redirectUri() });
    location.assign(`${dominio()}/logout?${params}`);
  }

  return { configurado, login, tratarRetorno, getUser, getIdToken, logout, redirectUri };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createAuth }; // testes em Node
} else {
  // eslint-disable-next-line no-var
  var Auth = createAuth({
    config: typeof COGNITO_CONFIG === 'object' ? COGNITO_CONFIG : {},
    storage: window.sessionStorage,
    location: window.location,
    history: window.history,
    fetchFn: (...args) => window.fetch(...args),
    cryptoObj: window.crypto,
    now: () => Date.now(),
  });
}
