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
      ws.onclose = () => {
        ws = null
        if (intentionalClose) return
        // Reconnect after a short delay — mirrors TermSession.vue's own backoff
        // for the same "session token may just be mid-refresh" transient case.
        setTimeout(() => this.connect(), 5000)
      }
    },
    disconnect() {
      intentionalClose = true
      ws?.close()
      ws = null
    },
  },
})
