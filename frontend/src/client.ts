export interface Config {
  apiUrl: string;
  websocketUrl?: string;
  cognitoDomain?: string;
  clientId?: string;
  local?: boolean;
}
export let config: Config = { apiUrl: "", local: true };
let token = sessionStorage.getItem("jimmy_token") || "";
export function hasSession() {
  return Boolean(token);
}
let initialization: Promise<void> | undefined;
export function initialize() {
  return (initialization ??= initializeSession());
}
async function initializeSession() {
  const response = await fetch("/config.json", { cache: "no-store" });
  if (response.ok && response.headers.get("content-type")?.includes("json"))
    config = await response.json();
  const code = new URL(location.href).searchParams.get("code");
  if (code && config.cognitoDomain && config.clientId) {
    const url = new URL(location.href);
    if (url.searchParams.get("state") !== sessionStorage.getItem("oauth_state"))
      throw Error("Sign-in state mismatch");
    const verifier = sessionStorage.getItem("oauth_verifier");
    if (!verifier) throw Error("Sign-in session expired");
    const result = await fetch(`${config.cognitoDomain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        code,
        redirect_uri: location.origin + "/",
        code_verifier: verifier,
      }),
    });
    if (!result.ok) throw Error("Sign-in failed");
    const idToken = (await result.json()).id_token;
    if (typeof idToken !== "string" || !idToken)
      throw Error("Sign-in did not return an identity token. Please try again.");
    token = idToken;
    sessionStorage.setItem("jimmy_token", token);
    sessionStorage.removeItem("oauth_verifier");
    sessionStorage.removeItem("oauth_state");
    history.replaceState({}, "", location.pathname);
  }
}
function b64(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}
export async function login() {
  const verifier = b64(crypto.getRandomValues(new Uint8Array(48)));
  const state = b64(crypto.getRandomValues(new Uint8Array(24)));
  sessionStorage.setItem("oauth_verifier", verifier);
  sessionStorage.setItem("oauth_state", state);
  const challenge = b64(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)),
    ),
  );
  location.href = `${config.cognitoDomain}/oauth2/authorize?${new URLSearchParams({ client_id: config.clientId!, response_type: "code", scope: "openid email profile", redirect_uri: location.origin + "/", state, code_challenge: challenge, code_challenge_method: "S256" })}`;
}
export function logout() {
  sessionStorage.removeItem("jimmy_token");
  token = "";
  location.reload();
}
export async function api<T = Record<string, unknown>>(
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(config.apiUrl + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "Request failed" }));
    throw Error(
      typeof err.detail === "string"
        ? err.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
export async function upload(
  project: string,
  file: File,
  kind: "paper" | "data",
) {
  const bytes = await file.arrayBuffer();
  const checksum = Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
  )
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
  const intent = await api<{
    id: string;
    url?: string;
    form?: { url: string; fields: Record<string, string> };
  }>(`/api/data/projects/${project}/uploads`, {
    name: file.name,
    kind,
    size: file.size,
    checksum,
  });
  let response: Response;
  if (intent.form) {
    const form = new FormData();
    Object.entries(intent.form.fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", file);
    response = await fetch(intent.form.url, { method: "POST", body: form });
  } else
    response = await fetch(config.apiUrl + intent.url, {
      method: "PUT",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: bytes,
    });
  if (!response.ok) throw Error("File upload failed");
  return api(`/api/data/projects/${project}/uploads/${intent.id}/complete`, {});
}
export async function liveSubscribe(project: string, invalidate: () => void) {
  if (!config.websocketUrl) return () => {};
  const ticket = await api<{ ticket: string }>(
    "/api/activity/realtime-ticket",
    {},
  );
  const socket = new WebSocket(
    `${config.websocketUrl}?ticket=${encodeURIComponent(ticket.ticket)}`,
  );
  socket.onopen = () => {
    socket.send(JSON.stringify({ action: "subscribe", project_id: project }));
    invalidate();
  };
  socket.onmessage = invalidate;
  socket.onerror = () => socket.close();
  return () => socket.close();
}
