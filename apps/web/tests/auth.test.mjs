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
  const localValues = new Map();
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: key => localValues.get(key) ?? null,
    setItem: (key, value) => localValues.set(key, String(value)),
    removeItem: key => localValues.delete(key),
  }});
  globalThis.crypto ??= webcrypto;
  globalThis.location = { href, origin: 'https://workspace.example', pathname: '/' };
  globalThis.history = { replaceState: (_state, _title, path) => { location.href = new URL(path, location.origin).href; }};
  return values;
}
const config = { apiUrl: 'https://api.example', cognitoDomain: 'https://auth.example', clientId: 'public-client' };
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
    return response({ id_token: 'header.' + Buffer.from(JSON.stringify({ sub: 'alice', exp: Math.floor(Date.now()/1000) + 600 })).toString('base64url') + '.signature' });
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

for (const [action, path] of [['login', '/oauth2/authorize'], ['signup', '/signup']]) {
  test(`${action} uses the Cognito OAuth code flow with S256 and state`, async () => {
    browser();
    globalThis.fetch = async () => response(config);
    const auth = await client();
    await auth.initialize();
    assert.equal(auth.hasSession(), false);
    await auth[action]();
    const url = new URL(location.href);
    assert.equal(url.origin, 'https://auth.example');
    assert.equal(url.pathname, path);
    assert.equal(url.searchParams.get('response_type'), 'code');
    assert.equal(url.searchParams.get('client_id'), config.clientId);
    assert.equal(url.searchParams.get('state'), sessionStorage.getItem('oauth_state'));
    const expected = Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(sessionStorage.getItem('oauth_verifier')))).toString('base64url');
    assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
    assert.equal(url.searchParams.get('code_challenge'), expected);
  });
}

test('expired sessions are removed from discovery instead of repeatedly invoking an empty runtime', async () => {
  browser();
  sessionStorage.setItem('jimmy_token', 'header.' + Buffer.from(JSON.stringify({ sub: 'alice', exp: Date.now()/1000 + 600 })).toString('base64url') + '.signature');
  localStorage.setItem('jimmy-runs-public-client-alice', JSON.stringify(['expired-run']));
  let invocations = 0;
  globalThis.fetch = async url => {
    if (url === '/config.json') return response(config);
    invocations++;
    return new Response(JSON.stringify({ detail: 'Session expired' }), { status: 404 });
  };
  const auth = await client(); await auth.initialize();
  assert.deepEqual(await auth.api('/api/paper-replication/runs'), []);
  assert.deepEqual(await auth.api('/api/paper-replication/runs'), []);
  assert.equal(invocations, 1);
});

test('completed run summaries do not keep their AgentCore sessions alive', async () => {
  browser();
  sessionStorage.setItem('jimmy_token', 'header.' + Buffer.from(JSON.stringify({ sub: 'alice', exp: Date.now()/1000 + 600 })).toString('base64url') + '.signature');
  localStorage.setItem('jimmy-runs-public-client-alice', JSON.stringify(['finished-run']));
  localStorage.setItem('jimmy-runs-public-client-alice-summaries', JSON.stringify({
    'finished-run': { id: 'finished-run', status: 'completed' },
  }));
  globalThis.fetch = async url => { assert.equal(url, '/config.json'); return response(config); };
  const auth = await client(); await auth.initialize();
  assert.deepEqual(await auth.api('/api/paper-replication/runs'), [{ id: 'finished-run', status: 'completed' }]);
});

test('API requests attach the identity token and preserve multipart uploads', async () => {
  browser();
  const token = 'header.' + Buffer.from(JSON.stringify({ sub: 'alice', exp: Date.now()/1000 + 600 })).toString('base64url') + '.signature';
  sessionStorage.setItem('jimmy_token', token);
  const body = new FormData(); body.append('paper', new Blob(['fixture']), 'paper.pdf');
  globalThis.fetch = async (url, init) => {
    if (url === '/config.json') return response(config);
    assert.equal(init.headers.get('Authorization'), `Bearer ${token}`);
    assert.equal(init.body, body);
    assert.equal(init.headers.has('Content-Type'), false);
    return response({ id: 'run', status: 'running' });
  };
  const auth = await client(); await auth.initialize();
  await auth.api('/api/paper-replication/runs', { method: 'POST', body });
});

test('legacy local configuration cannot bypass Cognito setup', async () => {
  browser();
  globalThis.fetch = async () => response({ apiUrl: '', local: true });
  const auth = await client();
  await assert.rejects(auth.initialize(), /Cognito configuration is incomplete/);
  assert.equal(auth.hasSession(), false);
});

test('legacy local flag cannot establish a signed-out session', async () => {
  browser();
  globalThis.fetch = async () => response({ ...config, local: true });
  const auth = await client();
  await auth.initialize();
  assert.equal(auth.hasSession(), false);
});
