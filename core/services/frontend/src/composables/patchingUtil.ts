import api from '@/api/client'

// Shared helpers for the patching views. Every string from the server is rendered as text.
export const detailOf = (e: any, fallback: string): string => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d : fallback
}
export const isDown = (e: any) => [404, 502, 503, 504].includes(e?.response?.status) || (!e?.response && !!e?.request)
export const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—')
export function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
export const label = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : '')
export const rolloutKind = (s: string) =>
  ({ pending_approval: 'pend', approved: 'info', running: 'info', paused: 'warn', halted: 'bad', completed: 'ok', failed: 'bad', rejected: 'off', cancelled: 'off' } as Record<string, string>)[s] || 'off'
export const hostKind = (s: string) =>
  ({ pending: 'off', running: 'info', done: 'ok', failed: 'bad', rolled_back: 'warn', skipped: 'off', cancelled: 'off' } as Record<string, string>)[s] || 'off'
export const opKind = (s: string) =>
  ({ queued: 'pend', awaiting_approval: 'pend', running: 'info', ok: 'ok', success: 'ok', done: 'ok', completed: 'ok', failed: 'bad', error: 'bad', cancelled: 'off', timeout: 'bad' } as Record<string, string>)[s] || 'off'
export const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const dayList = (d: number[]) => (d || []).map((x) => DAYS[x]?.slice(0, 3)).join(', ')

// Fetch through the authenticated client (a bare link would carry no bearer token) and save the blob.
export async function downloadBlob(url: string, params: Record<string, any>, filename: string) {
  const r = await api.get(url, { params, responseType: 'blob' })
  const href = URL.createObjectURL(r.data as Blob)
  const a = document.createElement('a')
  a.href = href; a.download = filename
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}
