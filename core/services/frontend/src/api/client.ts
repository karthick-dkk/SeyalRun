import axios, { AxiosHeaders } from 'axios'
import { getActivePinia } from 'pinia'

// In-memory only — never localStorage, never a cookie. This is deliberate:
// SeyalRun is iframe-embedded inside Zabbix, and a cookie set from within
// that third-party-iframe context is blocked by Safari ITP today (and
// increasingly Chrome), so a persistent store isn't viable here anyway. The
// accepted cost: a hard page reload with nothing in flight requires a fresh
// login — there is intentionally nothing to restore from.
let _token: string | null = null

export function getToken(): string | null {
  return _token
}

export function setToken(token: string) {
  _token = token
  _startHeartbeat()
}

export function clearToken() {
  _token = null
  _stopHeartbeat()
}

const api = axios.create({
  baseURL: '/api/v1',
})

api.interceptors.request.use((config) => {
  const token = getToken()
  if (token) {
    config.headers = config.headers ?? new AxiosHeaders()
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

let _401handled = false

api.interceptors.response.use(
  (response) => {
    _401handled = false
    return response
  },
  (error) => {
    if (error.response?.status === 401 && !_401handled) {
      _401handled = true
      clearToken()
      // Clear Pinia auth state without a circular import from auth.ts
      const pinia = getActivePinia()
      if (pinia?.state.value?.auth) {
        pinia.state.value.auth.user = null
      }
      if (window.location.hash !== '#/login') {
        window.location.hash = '#/login'
      }
    }
    return Promise.reject(error)
  },
)

export default api

// ── Heartbeat ────────────────────────────────────────────────────────────
// The session slides its idle timeout forward on any authenticated request,
// but a user working entirely inside an open terminal WebSocket never fires
// one (the WS is authenticated once at handshake and never touches the main
// REST session again). This heartbeat is what keeps "actively using the
// console" meaning something concrete: it only runs while the tab is
// visible, so backgrounding/closing the tab lets the session age out at the
// real idle timeout instead of being artificially kept alive forever.
const HEARTBEAT_INTERVAL_MS = 2 * 60 * 1000
let _heartbeatTimer: ReturnType<typeof setInterval> | null = null

function _startHeartbeat() {
  _stopHeartbeat()
  _heartbeatTimer = setInterval(() => {
    if (document.visibilityState === 'visible' && getToken()) {
      api.post('/auth/touch').catch(() => {})
    }
  }, HEARTBEAT_INTERVAL_MS)
}

function _stopHeartbeat() {
  if (_heartbeatTimer) {
    clearInterval(_heartbeatTimer)
    _heartbeatTimer = null
  }
}

/** Build a WebSocket URL with the current session/PAT token as a query param.
 *  path should be like "ssh/some-session-id" (no leading slash, no /ws/ prefix).
 */
export function wsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken() ?? ''
  return `${proto}://${location.host}/ws/${path}?token=${encodeURIComponent(token)}`
}

/** Build a hash-route URL to the terminal that hands the current session token to the
 *  new tab via the URL fragment (`_session`). Necessary regardless of storage model —
 *  an in-memory token can never be shared tab-to-tab by definition, so a brand new
 *  browsing context (e.g. opened from the Zabbix iframe) needs it passed explicitly.
 *  The token lives only in the fragment (never sent to the server) and the router
 *  strips it from the address bar immediately.
 */
export function terminalUrl(query = ''): string {
  const tok = getToken()
  const parts = [query, tok ? `_session=${encodeURIComponent(tok)}` : ''].filter(Boolean)
  return `/#/terminal${parts.length ? '?' + parts.join('&') : ''}`
}

/** In-app connect handshake.
 *
 *  A terminal opened from INSIDE the app (the Hosts/Assets connect icon) may honour a
 *  remembered login and connect automatically. A terminal reached by following a link
 *  from OUTSIDE (a Zabbix deep link, a pasted URL) must not — it always requires an
 *  explicit login choice, because a link can be followed by someone who did not choose
 *  it. Both open with the same `host_id=…&autoconnect=1` URL (the Zabbix host module
 *  builds that exact shape too), so the URL cannot tell them apart, and a query flag
 *  would be forgeable by any such link. Instead the in-app opener drops a short-lived,
 *  same-origin marker that a cross-origin link physically cannot write; the terminal
 *  tab consumes it once. No marker → treat as a deep link (the safe default). */
const INTERNAL_CONNECT_TTL_MS = 15_000

export function markInternalConnect(hostId: string): void {
  try { localStorage.setItem(`sr_ti:${hostId}`, String(Date.now())) } catch { /* storage blocked (e.g. 3rd-party iframe) — falls back to the explicit picker */ }
}

export function consumeInternalConnect(hostId: string): boolean {
  try {
    const key = `sr_ti:${hostId}`
    const raw = localStorage.getItem(key)
    if (!raw) return false
    localStorage.removeItem(key)          // consume-on-read: the opener's own tab wins any race
    return Date.now() - Number(raw) < INTERNAL_CONNECT_TTL_MS
  } catch { return false }
}
