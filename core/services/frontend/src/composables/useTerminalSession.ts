/**
 * Session lifecycle for the terminal — Increment 1a, step 3.
 *
 * Connect, disconnect and reconnect for a pane, extracted from
 * TerminalView.vue with no behaviour change. Pairs with useTerminalPanes,
 * which owns pane/split state; this owns what happens inside a pane once a
 * host is chosen.
 *
 * The error mapping is the substance here. `POST /ssh/sessions` returns 403 for
 * several genuinely different reasons — no credential linked, the `ssh` action
 * not granted, a login ACL refusal, no authorization at all — and each one has
 * a different fix in a different admin screen. Collapsing them to "forbidden"
 * turns a two-minute fix into a support ticket, so the discrimination is kept
 * in one place rather than duplicated at each call site.
 */

import { reactive, type Ref } from 'vue'

import api from '@/api/client'
import type { Pane } from './useTerminalPanes'

export interface UseTerminalSessionOptions {
  panes: Ref<Pane[]>
  /** Host list, read when reconnecting to resolve a pane's hostId back to a host. */
  hosts: Ref<any[]>
}

/** 403 detail -> the message that names the screen the operator must go to. */
export function sshErrorMessage(status: number | undefined, detail: string, hostName: string): string {
  if (status === 403) {
    if (detail.includes('credential')) {
      return `No credential for "${hostName}" — link one in Admin → Credentials.`
    }
    if (detail.includes('ssh action')) {
      return `SSH not permitted for "${hostName}" — add "ssh" to its Authorization actions.`
    }
    if (detail.includes('ACL') || detail.includes('login denied')) {
      return `Login blocked by ACL for "${hostName}".`
    }
    return `No authorization for "${hostName}" — add one in Admin → Authorizations.`
  }
  if (status === 404) return `Host not found.`
  if (detail) return detail
  return `SSH connection failed to "${hostName}" — check the host is reachable and credentials are correct.`
}

export function useTerminalSession(opts: UseTerminalSessionOptions) {
  const { panes, hosts } = opts

  /** Host ids that dropped in the last 5s — drives the red dot in the host list. */
  const recentlyDisconnected = reactive(new Set<string>())

  function find(paneId: string): Pane | undefined {
    return panes.value.find(p => p.id === paneId)
  }

  async function connectPane(paneId: string, host: any, credentialId?: string) {
    const pane = find(paneId)
    if (!pane) return
    pane.error = null
    pane.disconnected = false
    pane.connecting = true
    pane.label = host.name
    pane.session = null
    const body: any = { host_id: host.id }
    if (credentialId) body.credential_id = credentialId
    try {
      const resp = await api.post('/ssh/sessions', body)
      pane.session = { session_id: resp.data.id, ws_path: resp.data.ws_path }
      pane.hostId = host.id
    } catch (e: any) {
      pane.error = sshErrorMessage(e.response?.status, e.response?.data?.detail ?? '', host.name)
      pane.label = host.name
    } finally {
      pane.connecting = false
    }
  }

  function onDisconnected(paneId: string) {
    const pane = find(paneId)
    if (!pane) return
    if (pane.hostId) {
      const hid = pane.hostId
      recentlyDisconnected.add(hid)
      setTimeout(() => recentlyDisconnected.delete(hid), 5000)
    }
    // Keep pane.session so TermSession stays mounted and shows its reconnect
    // overlay. disconnected=true is what makes onHostClick and the session-count
    // badge treat the pane as free rather than busy.
    pane.disconnected = true
    pane.label = 'Disconnected'
  }

  async function onReconnect(paneId: string) {
    const pane = find(paneId)
    if (!pane?.hostId) return
    const host = hosts.value.find(h => h.id === pane.hostId)
    if (!host) return
    // Clear the old session so TermSession unmounts cleanly before connectPane
    // remounts it — reusing the same session_id would not re-run its setup.
    pane.session = null
    pane.disconnected = false
    await connectPane(paneId, host)
  }

  return { recentlyDisconnected, connectPane, onDisconnected, onReconnect }
}
