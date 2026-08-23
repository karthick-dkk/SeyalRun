<template>
  <div class="sw">
    <header class="sw-head">
      <div class="sw-title">
        <span class="sw-live"></span>
        Watching <strong>{{ meta.username || '…' }}</strong>
        <span class="sw-dim">on {{ meta.host_name || '…' }}</span>
      </div>
      <div class="sw-actions">
        <span v-if="controller" class="sw-badge sw-badge--control">
          {{ controller === myName ? 'You have control' : `${controller} has control` }}
        </span>
        <span v-else class="sw-badge">Read-only</span>

        <button v-if="isAdmin && controller !== myName" class="btn btn-sm" :disabled="!connected" @click="takeover">
          Take Control
        </button>
        <button v-else-if="isAdmin" class="btn btn-sm" :disabled="!connected" @click="release">
          Release Control
        </button>
        <button v-if="isAdmin" class="btn btn-sm btn-danger" :disabled="!connected" @click="terminate">
          ✕ Terminate
        </button>
        <button class="btn btn-sm" @click="$router.back()">Close</button>
      </div>
    </header>

    <p v-if="notice" class="sw-notice">{{ notice }}</p>

    <div ref="hostEl" class="sw-term" @click="focusTerm"></div>

    <footer class="sw-foot">
      <span v-if="!connected" class="sw-dim">Disconnected</span>
      <span v-else-if="controller === myName" class="sw-dim">
        You are typing into this session — the operator has been notified and every command is recorded against you.
      </span>
      <span v-else class="sw-dim">Observation only. Keystrokes are not sent.</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
/**
 * Supervision view — Increment 2.
 *
 * Joins a live session read-only, and (for admins) can take control or end it.
 * Three deliberate properties:
 *
 *  - Joining is audited before any output is shown, so "who watched this
 *    session" is answerable. That was the gap: read-only join already worked and
 *    recorded nothing.
 *  - Taking control announces itself in the operator's own terminal. Silent
 *    control would attribute commands to the person whose session it is.
 *  - Input is only sent while this viewer actually holds control, and the server
 *    re-checks that on every frame — the button state here is a convenience, not
 *    the control.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

import api, { wsUrl } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useTerminalTheme } from '@/composables/useTerminalTheme'

const route = useRoute()
const auth = useAuthStore()
const { currentTheme } = useTerminalTheme()

const sessionId = String(route.params.id || '')
const hostEl = ref<HTMLElement | null>(null)
const connected = ref(false)
const controller = ref<string | null>(null)
const notice = ref('')
const meta = ref<Record<string, any>>({})

const myName = computed(() => auth.user?.username || '')
const isAdmin = computed(() => ['admin', 'superadmin'].includes(auth.user?.role_name || ''))

let term: Terminal | null = null
let fit: FitAddon | null = null
let ws: WebSocket | null = null

function focusTerm() { term?.focus() }

onMounted(async () => {
  try {
    const { data } = await api.get(`/ssh/sessions/${sessionId}`)
    meta.value = data
  } catch { /* header just shows placeholders */ }

  term = new Terminal({ fontSize: 13, theme: currentTheme.value, cursorBlink: false, scrollback: 5000 })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(hostEl.value!)
  fit.fit()
  window.addEventListener('resize', onResize)

  // Keystrokes go to the server only while we hold control; the server checks
  // again regardless, so a stale local flag cannot inject input.
  term.onData((d) => {
    if (controller.value === myName.value) ws?.send(JSON.stringify({ type: 'input', data: d }))
  })

  ws = new WebSocket(wsUrl(`ssh/${sessionId}/spectate`))
  ws.onopen = () => { connected.value = true }
  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data)
    if (msg.type === 'output') term!.write(msg.data)
    else if (msg.type === 'control') controller.value = msg.controller ?? null
    else if (msg.type === 'denied') notice.value = msg.message
    else if (msg.type === 'terminated') { notice.value = 'Session terminated.'; connected.value = false }
  }
  ws.onclose = () => { connected.value = false }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  ws?.close()
  term?.dispose()
})

function onResize() { fit?.fit() }

function takeover() { notice.value = ''; ws?.send(JSON.stringify({ type: 'takeover' })) }
function release()  { notice.value = ''; ws?.send(JSON.stringify({ type: 'release' })) }
function terminate() {
  if (!window.confirm('End this session for the operator immediately?')) return
  ws?.send(JSON.stringify({ type: 'terminate' }))
}
</script>

<style scoped>
.sw { display: flex; flex-direction: column; height: calc(100vh - 90px); gap: 8px; }
.sw-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.sw-title { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.sw-dim { color: var(--text2); font-weight: 400; }
.sw-live { width: 8px; height: 8px; border-radius: 50%; background: #f85149; box-shadow: 0 0 0 3px rgba(248,81,73,.18); }
.sw-actions { display: flex; align-items: center; gap: 6px; }
.sw-badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: var(--surface2); color: var(--text2); }
.sw-badge--control { background: #1f6feb; color: #fff; }
.sw-notice { margin: 0; padding: 6px 10px; border-radius: 6px; background: #3d1418; border: 1px solid #6e2b31; color: #ffa198; font-size: 12px; }
.sw-term { flex: 1; min-height: 0; background: #1a1b1e; border: 1px solid var(--border); border-radius: 8px; padding: 6px; overflow: hidden; }
.sw-foot { font-size: 12px; }
@media (prefers-reduced-motion: reduce) { .sw-live { box-shadow: none; } }
</style>
