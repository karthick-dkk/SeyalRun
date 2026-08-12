import { defineStore } from 'pinia'

export interface CaptureItem {
  id: string
  dataUrl: string   // base64 PNG — small enough to hold a handful in memory
  label: string      // e.g. the terminal session id's short form, for context
  createdAt: number  // epoch ms
}

// In-memory only, same trade-off this app already makes for the auth token
// (api/client.ts) — a capture is meant to be used right away (paste it
// somewhere), not survive a hard reload. Auto-expires after an hour so a
// stale screenshot of a since-closed session doesn't linger indefinitely.
const EXPIRY_MS = 60 * 60 * 1000
const MAX_ITEMS = 10

export const useCapturesStore = defineStore('captures', {
  state: () => ({
    items: [] as CaptureItem[],
    _pruneTimer: null as ReturnType<typeof setInterval> | null,
  }),
  getters: {
    active(state): CaptureItem[] {
      const cutoff = Date.now() - EXPIRY_MS
      return state.items.filter((i) => i.createdAt >= cutoff)
    },
  },
  actions: {
    add(dataUrl: string, label: string) {
      this.items.unshift({ id: crypto.randomUUID(), dataUrl, label, createdAt: Date.now() })
      this.items = this.items.slice(0, MAX_ITEMS)
      this._ensurePruneTimer()
    },
    remove(id: string) {
      this.items = this.items.filter((i) => i.id !== id)
    },
    async copy(id: string): Promise<boolean> {
      const item = this.items.find((i) => i.id === id)
      if (!item) return false
      try {
        const blob = await (await fetch(item.dataUrl)).blob()
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })])
        return true
      } catch {
        return false
      }
    },
    _ensurePruneTimer() {
      if (this._pruneTimer) return
      this._pruneTimer = setInterval(() => {
        const cutoff = Date.now() - EXPIRY_MS
        this.items = this.items.filter((i) => i.createdAt >= cutoff)
      }, 60_000)
    },
  },
})
