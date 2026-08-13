import { defineStore } from 'pinia'
import api, { wsUrl } from '@/api/client'

export interface AppNotification {
  id: string
  severity: 'info' | 'medium' | 'critical'
  title: string
  message: string
  source_type: string
  source_id: string | null
  read: boolean
  created_at: string | null
}

let ws: WebSocket | null = null
// Reconnect state. A fixed 5s retry with no ceiling meant a server that keeps
// rejecting — an expired token, a shell still mounted after logout — was
// reconnected against every 5 seconds indefinitely, and every client in a fleet
// retried in lockstep after a restart. Backoff bounds the first, jitter spreads
// the second, and the timer handle stops two chains stacking when connect() is
// called again while one is already pending.
let retryAttempt = 0
let retryTimer: ReturnType<typeof setTimeout> | null = null
const RETRY_BASE_MS = 2000
const RETRY_MAX_MS = 60000
let intentionalClose = false

export const useNotificationsStore = defineStore('notifications', {
  state: () => ({
    items: [] as AppNotification[],
    unreadCount: 0,
    mutedSeverities: [] as string[],
  }),
  actions: {
    async load() {
      const { data } = await api.get('/notifications', { params: { limit: 50 } })
      this.items = data.items
      this.unreadCount = data.unread_count
    },
    async loadPreferences() {
      const { data } = await api.get('/notifications/preferences')
      this.mutedSeverities = data.muted_severities
    },
    async setPreferences(muted: string[]) {
      const { data } = await api.put('/notifications/preferences', { muted_severities: muted })
      this.mutedSeverities = data.muted_severities
    },
    async markRead(id: string) {
      await api.post(`/notifications/${id}/read`)
      const n = this.items.find((i) => i.id === id)
      if (n && !n.read) { n.read = true; this.unreadCount = Math.max(0, this.unreadCount - 1) }
    },
    async markAllRead() {
      await api.post('/notifications/read-all')
      this.items.forEach((n) => { n.read = true })
      this.unreadCount = 0
    },
    async dismiss(id: string) {
      await api.delete(`/notifications/${id}`)
      const n = this.items.find((i) => i.id === id)
      this.items = this.items.filter((i) => i.id !== id)
      if (n && !n.read) this.unreadCount = Math.max(0, this.unreadCount - 1)
    },
    // Opened once at app-shell mount and kept alive across navigation — a fresh
    // per-page connection would miss notifications that arrive while browsing.
    connect() {
      if (ws) return
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
      intentionalClose = false
      ws = new WebSocket(wsUrl('notifications'))
      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data)
        if (msg.type !== 'notification') return
        this.items.unshift({
          id: msg.id, severity: msg.severity, title: msg.title, message: msg.message || '',
          source_type: msg.source_type, source_id: msg.source_id, read: false, created_at: msg.created_at,
        })
        this.items = this.items.slice(0, 50)
        this.unreadCount += 1
      }
      ws.onopen = () => { retryAttempt = 0 }   // a real connection resets the backoff
      ws.onclose = () => {
        ws = null
        if (intentionalClose) return
        // Exponential backoff, capped, with jitter. The transient case this
        // exists for (a session token mid-refresh) clears in one or two tries;
        // anything that does not clear is a persistent rejection and must not be
        // retried at a fixed rate forever.
        const delay = Math.min(RETRY_BASE_MS * 2 ** retryAttempt, RETRY_MAX_MS)
        retryAttempt += 1
        const jittered = delay * (0.5 + Math.random())
        if (retryTimer) clearTimeout(retryTimer)
        retryTimer = setTimeout(() => { retryTimer = null; this.connect() }, jittered)
      }
    },
    disconnect() {
      intentionalClose = true
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
      retryAttempt = 0
      ws?.close()
      ws = null
    },
  },
})
