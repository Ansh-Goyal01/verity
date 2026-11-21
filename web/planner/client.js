const configured = window.VERITY_CONFIG?.apiBaseUrl;
const loopback = ['localhost', '127.0.0.1'].includes(location.hostname);
// The launcher serves the static site on 5500. Never send credentials to a host supplied in a link.
const base = configured || (loopback && location.port === '5500' ? 'http://127.0.0.1:8000' : location.origin);
const storageKey = `verity.session:${base}`;
let session;
try { session = JSON.parse(sessionStorage.getItem(storageKey)); } catch { session = null; }
let renewal;
let authConfig;

async function jsonRequest(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {cache: 'no-store', credentials: 'omit', redirect: 'error', ...options});
  } catch {
    throw new Error('Could not reach the service. Check your connection and try again.');
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data?.message || data?.msg || data?.error_description || data?.error || data?.detail;
    const error = new Error(typeof message === 'string' ? message : `Request failed (${response.status}).`);
    error.status = response.status;
    error.code = data?.code || (!data?.id && data?.error);
    error.cachedTrips = Array.isArray(data?.cached_trips) ? data.cached_trips : [];
    throw error;
  }
  if (data === null) throw new Error('The live API is unavailable on this host.');
  return data;
}

export const hasSession = () => Boolean(session?.access_token);
export function configuration() {
  if (!authConfig) authConfig = jsonRequest(base + '/api/auth/config').catch(error => { authConfig = null; throw error; });
  return authConfig;
}
function setSession(value) {
  session = value ? {access_token: value.access_token, refresh_token: value.refresh_token,
    expires_at: value.expires_at || Math.floor(Date.now() / 1000) + value.expires_in} : null;
  try {
    if (session) sessionStorage.setItem(storageKey, JSON.stringify(session));
    else sessionStorage.removeItem(storageKey);
  } catch {
    // Keep the active session in memory when browser storage is disabled.
  }
}
async function authRequest(path, body, token) {
  const cfg = await configuration();
  if (!cfg.available) throw new Error('Account sign-in is unavailable on this deployment.');
  const headers = {apikey: cfg.publishable_key, 'Content-Type': 'application/json'};
  if (token) headers.Authorization = 'Bearer ' + token;
  return jsonRequest(cfg.url + '/auth/v1/' + path, {method: 'POST', headers, body: JSON.stringify(body)});
}
export async function signIn(email, password) {
  setSession(await authRequest('token?grant_type=password', {email, password}));
}
export async function signOut() {
  const token = session?.access_token;
  setSession(null);
  if (token) await authRequest('logout?scope=local', {}, token);
}
async function accessToken() {
  if (!session) throw new Error('Sign in to open this workspace.');
  if (session.expires_at > Date.now() / 1000 + 45) return session.access_token;
  if (!renewal) {
    const old = session;
    renewal = authRequest('token?grant_type=refresh_token', {refresh_token: old.refresh_token})
      .then(fresh => { if (session === old) setSession(fresh); })
      .catch(error => {
        if (session === old && [400, 401, 403].includes(error.status)) setSession(null);
        throw error;
      }).finally(() => { renewal = null; });
  }
  await renewal;
  if (!session) throw new Error('Your session has ended. Sign in again.');
  return session.access_token;
}
export async function api(path, {method = 'GET', body, private: authenticated = false, writeKey} = {}) {
  const headers = {};
  if (writeKey) headers['Idempotency-Key'] = writeKey;
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
  const token = authenticated ? await accessToken() : null;
  if (token) headers.Authorization = 'Bearer ' + token;
  try {
    return await jsonRequest(base + '/api' + path, {method, headers, body: multipart ? body : JSON.stringify(body)});
  } catch (error) {
    if (authenticated && error.status === 401 && session?.access_token === token) {
      setSession(null);
      throw new Error('Your session expired. Open My account to sign in again.');
    }
    throw error;
  }
}
export const accountApi = (path, options = {}) => api(path, {...options, private: true});

// Keep a retry tied to its original account and operation, including across a page reload.
export function keyedWrite(user, path, body) {
  const slot = JSON.stringify(['verity.write',base,user,path,body]);
  let key = sessionStorage.getItem(slot);
  if (!key) { key = crypto.randomUUID(); sessionStorage.setItem(slot,key); }
  return accountApi(path,{method:'POST',body,writeKey:key}).then(result => {
    if (sessionStorage.getItem(slot) === key) sessionStorage.removeItem(slot);
    return result;
  });
}
