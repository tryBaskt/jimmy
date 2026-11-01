export interface Config {
  apiUrl: string;
  cognitoDomain?: string;
  clientId?: string;
  workflows?: { id: string; name: string; api_prefix: string; requires_openai: boolean }[];
}
export let config: Config = { apiUrl: '' };
let token = sessionStorage.getItem('jimmy_token') || '';
let initialization: Promise<void> | undefined;
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}
const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
function claims() {
  try { return JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); }
  catch { return {}; }
}
export function hasSession() { return Boolean(token && claims().exp > Date.now() / 1000); }
export function initialize() { return initialization ??= initializeSession(); }
async function initializeSession() {
  const response = await fetch('/config.json', { cache: 'no-store' });
  if (!response.ok) throw Error('Workspace configuration is unavailable');
  config = await response.json();
  if (!config.cognitoDomain || !config.clientId) throw Error('Cognito configuration is incomplete');
  const url = new URL(location.href);
  if (url.searchParams.has('error')) throw Error(url.searchParams.get('error_description') || 'Sign-in failed');
  const code = url.searchParams.get('code');
  if (!code) return;
  if (url.searchParams.get('state') !== sessionStorage.getItem('oauth_state')) throw Error('Sign-in state mismatch');
  const verifier = sessionStorage.getItem('oauth_verifier');
  if (!verifier) throw Error('Sign-in session expired');
  const result = await fetch(`${config.cognitoDomain}/oauth2/token`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: config.clientId!, code,
      redirect_uri: location.origin + '/', code_verifier: verifier }),
  });
  if (!result.ok) throw Error('Sign-in failed');
  const data = await result.json();
  if (typeof data.id_token !== 'string' || !data.id_token) throw Error('Sign-in did not return an identity token');
  token = data.id_token;
  if (!hasSession()) { token = ''; throw Error('Sign-in returned an expired identity token'); }
  sessionStorage.setItem('jimmy_token', token);
  sessionStorage.removeItem('oauth_state'); sessionStorage.removeItem('oauth_verifier');
  history.replaceState({}, '', '/');
}
export async function login() {
  const verifier = b64(crypto.getRandomValues(new Uint8Array(48)));
  const state = b64(crypto.getRandomValues(new Uint8Array(24)));
  sessionStorage.setItem('oauth_verifier', verifier); sessionStorage.setItem('oauth_state', state);
  const challenge = b64(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
  location.href = `${config.cognitoDomain}/oauth2/authorize?${new URLSearchParams({
    client_id: config.clientId!, response_type: 'code', scope: 'openid email profile',
    redirect_uri: location.origin + '/', state, code_challenge: challenge, code_challenge_method: 'S256',
  })}`;
}
export function logout() {
  token = ''; sessionStorage.removeItem('jimmy_token');
  location.href = config.cognitoDomain ? `${config.cognitoDomain}/logout?${new URLSearchParams({
    client_id: config.clientId!, logout_uri: location.origin + '/',
  })}` : '/';
}
function runKey() { return `jimmy-runs-${config.clientId || 'unconfigured'}-${claims().sub || 'anonymous'}`; }
async function request(path: string, init?: RequestInit) {
  if (!hasSession()) { logout(); throw Error('Please sign in again'); }
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(config.apiUrl + path, { ...init, headers });
  if (response.status === 401) { logout(); throw Error('Please sign in again'); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`, response.status);
  }
  return response;
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Run discovery remains browser-local until the persistent data layer is implemented.
  if (path === '/api/paper-replication/runs' && !init?.method) {
    const ids: string[] = JSON.parse(localStorage.getItem(runKey()) || '[]');
    const cache = JSON.parse(localStorage.getItem(runKey() + '-summaries') || '{}');
    const entries = await Promise.allSettled(ids.map(id => cache[id] && cache[id].status !== 'running'
      ? Promise.resolve(cache[id]) : api(`/api/paper-replication/runs/${id}`)));
    const retained = ids.filter((_, i) => !(entries[i].status === 'rejected' &&
      (entries[i] as PromiseRejectedResult).reason.status === 404));
    localStorage.setItem(runKey(), JSON.stringify(retained));
    return entries.filter(e => e.status === 'fulfilled').map(e => e.value) as T;
  }
  const result = await (await request(path, init)).json();
  if (path === '/api/paper-replication/runs' && init?.method === 'POST') {
    const ids = JSON.parse(localStorage.getItem(runKey()) || '[]');
    localStorage.setItem(runKey(), JSON.stringify([result.id, ...ids.filter((id: string) => id !== result.id)].slice(0, 20)));
  }
  if (result.id && result.status && path.startsWith('/api/paper-replication/runs')) {
    const cache = JSON.parse(localStorage.getItem(runKey() + '-summaries') || '{}');
    const { id, status, created_at, filename, title, completed_experiments, failed_experiments, report_ready } = result;
    cache[id] = { id, status, created_at, filename, title, completed_experiments, failed_experiments, report_ready };
    localStorage.setItem(runKey() + '-summaries', JSON.stringify(cache));
  }
  return result;
}
export async function download(path: string) {
  const response = await request(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url;
  link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'jimmy-report';
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
