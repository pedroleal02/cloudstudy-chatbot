'use strict';

/*
 * Testes do módulo de autenticação. Nenhuma chamada real: storage, location,
 * fetch e relógio são simulados (sem custo, sem rede).
 * Requer Node 18+. Rodar:  node --test frontend/auth.test.jsauth.test.js
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { createAuth } = require('./auth.js');

const CONFIG = {
  region: 'us-east-1',
  userPoolId: 'us-east-1_AbCdEfGhI',
  clientId: 'client123',
  domain: 'https://cloudstudy-teste.auth.us-east-1.amazoncognito.com/',
};
const AGORA = 1_800_000_000_000; // ms
const ISS = `https://cognito-idp.${CONFIG.region}.amazonaws.com/${CONFIG.userPoolId}`;

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}

function jwt(extra = {}) {
  const payload = {
    sub: 'u-1', email: 'ana@exemplo.com', aud: CONFIG.clientId, iss: ISS,
    token_use: 'id', exp: AGORA / 1000 + 3600, ...extra,
  };
  return `${b64url({ alg: 'RS256' })}.${b64url(payload)}.assinatura`;
}

function montar({ search = '', fetchFn, config = CONFIG } = {}) {
  const mem = new Map();
  const storage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: (k) => mem.delete(k),
  };
  const location = {
    origin: 'http://localhost:8000', pathname: '/index.html', search,
    assigned: null, assign(u) { this.assigned = u; },
  };
  const history = { url: null, replaceState(_s, _t, u) { this.url = u; } };
  const auth = createAuth({
    config, storage, location, history, fetchFn, cryptoObj: webcrypto, now: () => AGORA,
  });
  return { auth, storage, location, history };
}

// ---------- login ----------

test('login monta a URL de autorização com PKCE S256 e sem segredo', async () => {
  const { auth, location, storage } = montar();
  await auth.login();

  const url = new URL(location.assigned);
  assert.equal(url.origin, 'https://cloudstudy-teste.auth.us-east-1.amazoncognito.com');
  assert.equal(url.pathname, '/oauth2/authorize');
  assert.equal(url.searchParams.get('response_type'), 'code');
  assert.equal(url.searchParams.get('client_id'), 'client123');
  assert.equal(url.searchParams.get('redirect_uri'), 'http://localhost:8000/'); // index.html removido
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
  assert.ok(url.searchParams.get('code_challenge').length >= 43);
  assert.ok(url.searchParams.get('state').length >= 16);
  assert.equal(url.searchParams.has('client_secret'), false);

  // o verifier fica guardado, mas nunca vai na URL
  const pkce = JSON.parse(storage.getItem('cloudstudy.pkce'));
  assert.ok(pkce.verifier.length >= 43);
  assert.ok(!location.assigned.includes(pkce.verifier));
});

test('code_challenge segue o vetor de teste da RFC 7636 (Apêndice B)', async () => {
  // Fixa o random para reproduzir o verifier do exemplo da RFC.
  const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk';
  const bytes = Buffer.from(verifier, 'base64url');
  const cryptoFixo = {
    subtle: webcrypto.subtle,
    getRandomValues: (a) => { a.set(bytes.subarray(0, a.length)); return a; },
  };
  const mem = new Map();
  const location = { origin: 'http://localhost:8000', pathname: '/', search: '', assign(u) { this.assigned = u; } };
  const auth = createAuth({
    config: CONFIG,
    storage: { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k) },
    location, history: {}, fetchFn: null, cryptoObj: cryptoFixo, now: () => AGORA,
  });
  await auth.login();
  const challenge = new URL(location.assigned).searchParams.get('code_challenge');
  // o verifier gerado aqui tem 32 bytes (43 chars) = o do exemplo da RFC
  assert.equal(challenge, 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM');
});

test('login sem configuração dá um erro claro e não redireciona', async () => {
  const { auth, location } = montar({ config: { region: 'us-east-1' } });
  assert.equal(auth.configurado(), false);
  await assert.rejects(() => auth.login(), /não está configurado/);
  assert.equal(location.assigned, null);
});

// ---------- retorno do Cognito ----------

test('sem code/error na URL não faz nada', async () => {
  const { auth, history } = montar();
  assert.deepEqual(await auth.tratarRetorno(), { status: 'nenhum' });
  assert.equal(history.url, null);
});

test('retorno válido troca o código por tokens e limpa a URL', async () => {
  let chamada;
  const fetchFn = async (url, opts) => {
    chamada = { url, opts };
    return { ok: true, json: async () => ({ id_token: jwt(), access_token: 'acc', refresh_token: 'nao-guardar' }) };
  };
  const { auth, storage, history, location } = montar({ fetchFn });
  await auth.login();
  const state = new URL(location.assigned).searchParams.get('state');
  const verifier = JSON.parse(storage.getItem('cloudstudy.pkce')).verifier;

  location.search = `?code=abc&state=${state}`; // o Cognito devolve o usuário com code e state
  const r = await auth.tratarRetorno();

  assert.equal(r.status, 'ok');
  assert.equal(history.url, 'http://localhost:8000/');
  assert.equal(chamada.url, 'https://cloudstudy-teste.auth.us-east-1.amazoncognito.com/oauth2/token');
  const corpo = new URLSearchParams(chamada.opts.body);
  assert.equal(corpo.get('grant_type'), 'authorization_code');
  assert.equal(corpo.get('code'), 'abc');
  assert.equal(corpo.get('code_verifier'), verifier);
  assert.equal(corpo.has('client_secret'), false);
  assert.equal(storage.getItem('cloudstudy.pkce'), null); // uso único
  assert.ok(!storage.getItem('cloudstudy.tokens').includes('nao-guardar')); // refresh token não é guardado
  assert.deepEqual(auth.getUser(), { sub: 'u-1', email: 'ana@exemplo.com' });
});

test('state diferente é recusado (proteção contra CSRF) e não chama o token endpoint', async () => {
  let chamou = false;
  const fetchFn = async () => { chamou = true; return { ok: true, json: async () => ({}) }; };
  const { auth, location } = montar({ fetchFn });
  await auth.login();
  location.search = '?code=abc&state=forjado';
  const r = await auth.tratarRetorno();
  assert.equal(r.status, 'erro');
  assert.equal(chamou, false);
  assert.equal(auth.getUser(), null);
});

test('retorno sem login iniciado nesta aba é recusado', async () => {
  const { auth } = montar({ search: '?code=abc&state=qualquer', fetchFn: async () => { throw new Error('não deveria chamar'); } });
  assert.equal((await auth.tratarRetorno()).status, 'erro');
});

test('?error= do Cognito vira mensagem amigável', async () => {
  const { auth, history } = montar({ search: '?error=access_denied&error_description=x' });
  const r = await auth.tratarRetorno();
  assert.equal(r.status, 'erro');
  assert.match(r.mensagem, /cancelado|recusado/);
  assert.equal(history.url, 'http://localhost:8000/');
});

test('falha no token endpoint não guarda tokens', async () => {
  const fetchFn = async () => ({ ok: false, json: async () => ({ error: 'invalid_grant' }) });
  const { auth, location } = montar({ fetchFn });
  await auth.login();
  location.search = `?code=abc&state=${new URL(location.assigned).searchParams.get('state')}`;
  const r = await auth.tratarRetorno();
  assert.equal(r.status, 'erro');
  assert.equal(auth.getUser(), null);
});

test('falha de rede no token endpoint dá mensagem amigável', async () => {
  const fetchFn = async () => { throw new TypeError('network'); };
  const { auth, location } = montar({ fetchFn });
  await auth.login();
  location.search = `?code=abc&state=${new URL(location.assigned).searchParams.get('state')}`;
  const r = await auth.tratarRetorno();
  assert.equal(r.status, 'erro');
  assert.match(r.mensagem, /serviço de login/);
});

// ---------- validação do token ----------

function comToken(extra, ajusteConfig) {
  const m = montar({ config: { ...CONFIG, ...ajusteConfig } });
  m.storage.setItem('cloudstudy.tokens', JSON.stringify({ idToken: jwt(extra), accessToken: 'a' }));
  return m.auth;
}

test('token válido identifica o usuário', () => {
  assert.equal(comToken({}).getUser().email, 'ana@exemplo.com');
  assert.ok(comToken({}).getIdToken());
});

test('token expirado não conta como sessão', () => {
  assert.equal(comToken({ exp: AGORA / 1000 - 1 }).getUser(), null);
});

test('token de outro app client (aud) ou outro pool (iss) é recusado', () => {
  assert.equal(comToken({ aud: 'outro-client' }).getUser(), null);
  assert.equal(comToken({ iss: 'https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OUTRO' }).getUser(), null);
});

test('access token no lugar do id token é recusado', () => {
  assert.equal(comToken({ token_use: 'access' }).getUser(), null);
});

test('lixo no storage não quebra', () => {
  const m = montar();
  m.storage.setItem('cloudstudy.tokens', '{nao-e-json');
  assert.equal(m.auth.getUser(), null);
  m.storage.setItem('cloudstudy.tokens', JSON.stringify({ idToken: 'a.b.c' }));
  assert.equal(m.auth.getUser(), null);
});

// ---------- logout ----------

test('logout apaga a sessão e vai para o /logout do Cognito', () => {
  const m = montar();
  m.storage.setItem('cloudstudy.tokens', JSON.stringify({ idToken: jwt(), accessToken: 'a' }));
  m.auth.logout();
  assert.equal(m.storage.getItem('cloudstudy.tokens'), null);
  const url = new URL(m.location.assigned);
  assert.equal(url.pathname, '/logout');
  assert.equal(url.searchParams.get('client_id'), 'client123');
  assert.equal(url.searchParams.get('logout_uri'), 'http://localhost:8000/');
});
