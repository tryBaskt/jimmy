import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

let instance = 0;
function browser(href = 'https://workspace.example/') {
  const values = new Map();
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  }});
  globalThis.crypto ??= webcrypto;
  globalThis.location = { href, origin: 'https://workspace.example', pathname: '/' };
  globalThis.history = { replaceState: (_state, _title, path) => { location.href = new URL(path, location.origin).href; }};
  return values;
}
const config = { local: false, apiUrl: 'https://api.example', cognitoDomain: 'https://auth.example', clientId: 'public-client' };
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const client = () => import(`../src/client.ts?test=${instance++}`);

test('a callback is exchanged once even when initialization is invoked concurrently', async () => {
  browser('https://workspace.example/?code=dummy-code&state=dummy-state');
  sessionStorage.setItem('oauth_state', 'dummy-state');
  sessionStorage.setItem('oauth_verifier', 'dummy-verifier');
  let exchanges = 0;
  globalThis.fetch = async (url, request) => {
    if (url === '/config.json') return response(config);
    assert.equal(url, 'https://auth.example/oauth2/token');
    assert.equal(request.body.get('code_verifier'), 'dummy-verifier');
    assert.equal(request.body.get('redirect_uri'), 'https://workspace.example/');
    exchanges++;
    return response({ id_token: 'dummy-identity-token' });
  };
  const auth = await client();
  await Promise.all([auth.initialize(), auth.initialize()]);
  assert.equal(exchanges, 1);
  assert.equal(auth.hasSession(), true);
  assert.equal(sessionStorage.getItem('oauth_verifier'), null);
  assert.equal(location.href, 'https://workspace.example/');
});

test('state mismatch cannot exchange a code or establish a session', async () => {
  browser('https://workspace.example/?code=dummy-code&state=unexpected');
  sessionStorage.setItem('oauth_state', 'expected');
  globalThis.fetch = async url => { assert.equal(url, '/config.json'); return response(config); };
  const auth = await client();
  await assert.rejects(auth.initialize(), /state mismatch/);
  assert.equal(auth.hasSession(), false);
});

test('a response without an identity token is rejected instead of storing undefined', async () => {
  browser('https://workspace.example/?code=dummy-code&state=state');
  sessionStorage.setItem('oauth_state', 'state');
  sessionStorage.setItem('oauth_verifier', 'verifier');
  globalThis.fetch = async url => response(url === '/config.json' ? config : { access_token: 'dummy-access' });
  const auth = await client();
  await assert.rejects(auth.initialize(), /identity token/);
  assert.equal(auth.hasSession(), false);
  assert.equal(sessionStorage.getItem('jimmy_token'), null);
});

test('normal signed-out initialization has no session; login preserves S256 and state', async () => {
  browser();
  globalThis.fetch = async () => response(config);
  const auth = await client();
  await auth.initialize();
  assert.equal(auth.hasSession(), false);
  await auth.login();
  const url = new URL(location.href);
  assert.equal(url.origin, 'https://auth.example');
  assert.equal(url.searchParams.get('state'), sessionStorage.getItem('oauth_state'));
  const expected = Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(sessionStorage.getItem('oauth_verifier')))).toString('base64url');
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(url.searchParams.get('code_challenge'), expected);
});
