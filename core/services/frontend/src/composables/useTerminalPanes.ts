/**
 * Pane and split state for the terminal — Increment 1a of the parity plan.
 *
 * Extracted from TerminalView.vue with no behaviour change, ahead of SFTP
 * (Increment 1) and supervision (Increment 2), because the plan is explicit
 * that those will not fit in a 1,273-line view without it becoming
 * unmaintainable. Split before adding, not after.
 *
 * This owns pane/split state and nothing else — no connecting, no menus, no
 * hosts. Session lifecycle stays with the caller, so `openSplit` returns the
 * pane it made rather than connecting it itself.
 */

import { computed, ref, type Ref } from 'vue'

export interface TerminalSession {
  session_id: string
  ws_path: string
}

export interface Pane {
  id: string
  label: string
  name?: string | null
  hostId: string | null
  session: TerminalSession | null
  disconnected: boolean    // session ended — show reconnect overlay
  error: string | null     // last connection error to show in pane
  connecting: boolean      // connecting in-progress spinner
  /** 'sftp' shows only the file browser. The SSH session still runs underneath —
   *  file transfer rides that connection — it is simply not displayed. */
  mode: 'ssh' | 'sftp'
}

export interface UseTerminalPanesOptions {
  /** Kiosk mode never gets a second pane. A getter, not a boolean, so the
   *  composable reads the live value rather than a snapshot taken at setup. */
  isKiosk: () => boolean
}

export function useTerminalPanes(opts: UseTerminalPanesOptions) {
  let _pid = 0
  const mkPane = (label = 'New'): Pane => ({
    id: `p${++_pid}`, label, name: null, hostId: null,
    session: null, disconnected: false, error: null, connecting: false, mode: 'ssh',
  })

  const panes: Ref<Pane[]> = ref([mkPane()])
  const activePaneId = ref(panes.value[0].id)

  const splitId = ref<string | null>(null)
  const splitDir = ref<'h' | 'v'>('h')
  const focusedSide = ref<'primary' | 'secondary'>('primary')

  const activePane = computed(() => panes.value.find(p => p.id === activePaneId.value) ?? null)
  const splitPane = computed(() => panes.value.find(p => p.id === splitId.value) ?? null)
  const focusedPane = computed(() =>
    focusedSide.value === 'secondary' ? splitPane.value : activePane.value)

  const activeSessionCount = computed(() =>
    panes.value.filter(p => p.session && !p.disconnected).length)

  const paneAreaClass = computed(() => {
    if (!splitId.value) return 'single'
    return splitDir.value === 'h' ? 'split-h' : 'split-v'
  })

  /** Single choke point for every "create a pane" path — kiosk mode never gets a
   *  second pane regardless of which caller reaches here. Returns the existing
   *  pane rather than null so callers need no special-case handling. */
  function addPane(label?: string): Pane {
    if (opts.isKiosk() && panes.value.length >= 1) return panes.value[0]
    const p = mkPane(label)
    panes.value.push(p)
    activePaneId.value = p.id
    return p
  }

  function closePane(paneId: string) {
    if (splitId.value === paneId) splitId.value = null
    const idx = panes.value.findIndex(p => p.id === paneId)
    if (idx === -1) return
    panes.value.splice(idx, 1)
    if (!panes.value.length) panes.value.push(mkPane())
    if (activePaneId.value === paneId) {
      activePaneId.value = panes.value[Math.min(idx, panes.value.length - 1)].id
    }
  }

  function findPane(paneId: string): Pane | undefined {
    return panes.value.find(p => p.id === paneId)
  }

  /**
   * Open a split and return the new secondary pane, or null in kiosk mode.
   *
   * addPane() makes whatever it creates the ACTIVE pane — a side effect every
   * other caller wants, and wrong here: the new pane is the secondary side, so
   * that reassignment has to be undone or it silently steals the primary slot
   * from whatever session was already showing there, unmounting it. Confirmed
   * live once: clicking Split appeared to reload every open session, because
   * both slots ended up pointing at the same new empty pane.
   *
   * doSplit() and ctxSplitConnect() each carried their own copy of that undo.
   * Two copies of a fix for a non-obvious bug is one copy away from a
   * regression, so the undo lives here now and there is no second version.
   */
  function openSplit(dir: 'h' | 'v', label?: string): Pane | null {
    if (opts.isKiosk()) return null
    const originalActiveId = activePaneId.value
    const p = addPane(label)
    activePaneId.value = originalActiveId
    splitId.value = p.id
    splitDir.value = dir
    focusedSide.value = 'secondary'
    return p
  }

  function exitSplit() {
    splitId.value = null
    focusedSide.value = 'primary'
  }

  return {
    panes, activePaneId, splitId, splitDir, focusedSide,
    activePane, splitPane, focusedPane, activeSessionCount, paneAreaClass,
    mkPane, addPane, closePane, findPane, openSplit, exitSplit,
  }
}
