// Shared by the Posture dashboard, the hosts list and the host page.

export interface PostureHost {
  host_id: string
  hostname: string
  name?: string
  os_family?: string
  distro?: string
  distro_version?: string
  kernel?: string
  arch?: string
  packages_installed?: number
  pending_updates?: number
  pending_updates_known?: boolean
  security_updates?: number
  reboot_required?: boolean
  reboot_method?: string
  collected_at?: string | null
  status: 'ok' | 'stale' | 'failed' | 'never'
  last_error?: string | null
}

// Older API versions do not send the flag: a missing one counts as known.
export const updatesKnown = (h: any) => !h || !h.collected_at || h.pending_updates_known !== false
export const distroLabel = (h: any) => [h.distro, h.distro_version].filter(Boolean).join(' ') || h.os_family || '—'
export const statusKind = (s: string) => ({ ok: 'ok', stale: 'warn', failed: 'bad', never: 'off' } as Record<string, string>)[s] || 'off'
export const statusLabel = (s: string) => ({ ok: 'Fresh', stale: 'Stale', failed: 'Failed', never: 'Never collected' } as Record<string, string>)[s] || s

export const SEVS = ['critical', 'high', 'medium', 'low', 'unknown']
export const cap = (x: string) => (x ? x.charAt(0).toUpperCase() + x.slice(1) : '')
// Severity is always shown as a word as well as a colour.
export const sevKind = (s: string) => ({ critical: 'bad', high: 'warn', medium: 'info', low: 'ok', unknown: 'off' } as Record<string, string>)[s] || 'off'
export const fStatusKind = (s: string) => ({ open: 'warn', fixed: 'ok', accepted: 'pend' } as Record<string, string>)[s] || 'off'

// 404 / 502 / 503 mean the posture service is not deployed (or not up) yet: friendly empty state.
export const isDown = (e: any) => [404, 502, 503, 504].includes(e?.response?.status) || (!e?.response && !!e?.request)
export const detailOf = (e: any, fallback: string) => e?.response?.data?.detail || fallback

export function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}
