<template>
  <AppShell>
    <div class="sessions-page">
      <div class="sessions-header">
        <div>
          <h2 class="page-title">Sessions</h2>
          <p class="page-sub text-muted">SSH session history and recordings</p>
        </div>
        <div class="header-actions">
          <div class="filter-tabs">
            <button v-for="f in filters" :key="f.value"
              :class="{ active: statusFilter === f.value }"
              @click="statusFilter = f.value; load()"
              class="filter-tab">
              {{ f.label }}
              <span class="filter-count" v-if="f.value === ''">{{ total }}</span>
            </button>
          </div>
          <button class="btn-refresh" @click="load" :disabled="loading" title="Refresh">↺</button>
        </div>
      </div>

      <div v-if="loading" class="loading">Loading sessions…</div>

      <div v-else-if="sessions.length === 0" class="empty-state">
        <div class="empty-icon">⊙</div>
        <p>No sessions found.</p>
        <p class="text-muted">SSH sessions appear here after you connect to a host from the Assets page.</p>
      </div>

      <div v-else class="sessions-table">
        <div class="table-header">
          <span>Status</span>
          <span>Host</span>
          <span>User / Account</span>
          <span>Started</span>
          <span>Duration</span>
          <span>Actions</span>
        </div>

        <div v-for="s in sessions" :key="s.id" class="table-row" :class="{ 'row-active': s.status === 'active' }">
          <span>
            <span class="status-pill" :class="`pill-${s.status}`">
              {{ s.status === 'active' ? '● Live' : s.status === 'closed' ? '○ Closed' : '✗ Error' }}
            </span>
          </span>

          <span>
            <div class="row-host">{{ s.asset_name || s.asset_address }}</div>
            <div class="row-ip mono text-muted">{{ s.asset_address }}</div>
          </span>

          <span>
            <div class="mono">{{ s.ssh_username }}</div>
            <div class="text-muted" style="font-size:11px">by {{ s.user }}</div>
          </span>

          <span class="text-muted">{{ formatTime(s.started_at) }}</span>

          <span class="mono">
            {{ s.duration_seconds ? formatDuration(s.duration_seconds) : (s.status === 'active' ? 'Live' : '—') }}
          </span>

          <span class="row-actions">
            <button v-if="s.status === 'active'" class="action-btn btn-join" @click="joinSession(s.id)">
              ⊙ Join
            </button>
            <button v-if="s.status === 'idle'" class="action-btn btn-resume" @click="resumeSession(s.id)">
              ↺ Resume
            </button>
            <button v-if="s.status === 'closed' && s.duration_seconds && s.duration_seconds > 1"
              class="action-btn btn-replay" @click="openReplay(s)">
              ▶ Replay
            </button>
            <button v-if="s.status === 'active' || s.status === 'idle'" class="action-btn btn-kill" @click="terminateSession(s.id)">
              ⏹ Kill
            </button>
          </span>
        </div>
      </div>

      <!-- Pagination -->
      <div class="pagination" v-if="total > limit">
        <button :disabled="offset === 0" @click="offset -= limit; load()">‹ Prev</button>
        <span>{{ Math.floor(offset/limit)+1 }} / {{ Math.ceil(total/limit) }}</span>
        <button :disabled="offset + limit >= total" @click="offset += limit; load()">Next ›</button>
      </div>
    </div>

    <!-- Replay Modal -->
    <div v-if="replaySession" class="modal-overlay" @click.self="replaySession = null">
      <div class="replay-modal">
        <div class="replay-header">
          <div>
            <h3>Session Replay</h3>
            <p class="replay-meta mono">
              {{ replaySession.ssh_username }}@{{ replaySession.asset_address }}
              · {{ formatDuration(replaySession.duration_seconds || 0) }}
              · {{ formatTime(replaySession.started_at) }}
            </p>
          </div>
          <div class="replay-controls">
            <button @click="replaySpeed = Math.max(0.5, replaySpeed - 0.5)" class="ctrl-btn">−</button>
            <span class="speed-label">{{ replaySpeed }}×</span>
            <button @click="replaySpeed = Math.min(10, replaySpeed + 0.5)" class="ctrl-btn">+</button>
            <button @click="isPlaying ? pauseReplay() : startReplay()" class="ctrl-btn ctrl-play">
              {{ isPlaying ? '⏸' : '▶' }}
            </button>
            <button @click="replaySession = null; stopReplay()" class="ctrl-btn">✕</button>
          </div>
        </div>
        <div ref="replayTermEl" class="replay-terminal" />
        <div class="replay-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: `${replayProgress}%` }" />
          </div>
          <span class="mono">{{ formatDuration(replayElapsed) }} / {{ formatDuration(replaySession.duration_seconds || 0) }}</span>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import { api } from '@/api/client'
import { useEscapeKey } from '@/composables/useEscapeKey'
import { useUiStore } from '@/stores/ui'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Session {
  id: string; user: string; asset_id: string; asset_name: string
  asset_address: string; ssh_username: string; status: string
  started_at: string; ended_at?: string; duration_seconds?: number
  command_count?: number
}

const router = useRouter()
const ui = useUiStore()

const sessions = ref<Session[]>([])
const total = ref(0)
const loading = ref(false)
const statusFilter = ref('')
const limit = 30
const offset = ref(0)

const filters = [
  { label: 'All', value: '' },
  { label: 'Live', value: 'active' },
  { label: 'Idle', value: 'idle' },
  { label: 'Closed', value: 'closed' },
]

const replaySession = ref<Session | null>(null)

useEscapeKey(() => {
  if (replaySession.value) { replaySession.value = null; stopReplay() }
})
const replayTermEl = ref<HTMLElement>()
const replaySpeed = ref(2)
const isPlaying = ref(false)
const replayProgress = ref(0)
const replayElapsed = ref(0)
let replayTerm: Terminal | null = null
let replayTimer: ReturnType<typeof setTimeout> | null = null

function formatTime(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}
function formatDuration(s: number) {
  if (!s) return '0s'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

async function load() {
  loading.value = true
  try {
    const resp = await api.get('/ssh/sessions', {
      params: { status: statusFilter.value || undefined, limit, offset: offset.value },
    })
    sessions.value = resp.data.items
    total.value = resp.data.total
  } finally {
    loading.value = false
  }
}

function joinSession(id: string) {
  window.open(`/terminal?session=${id}`, '_blank')
}

function resumeSession(id: string) {
  // Same as join — backend will detect it's idle and resume
  window.open(`/terminal?session=${id}`, '_blank')
}

async function terminateSession(id: string) {
  if (!confirm('Kill this active session?')) return
  await api.delete(`/ssh/sessions/${id}`)
  ui.success('Session terminated')
  load()
}

async function openReplay(s: Session) {
  replaySession.value = s
  stopReplay()
  await new Promise(r => setTimeout(r, 50))
  initReplayTerminal()
  startReplay()
}

function initReplayTerminal() {
  replayTerm?.dispose()
  replayTerm = new Terminal({
    theme: { background: '#010409', foreground: '#e6edf3', cursor: '#58a6ff' },
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    fontSize: 13,
    cursorBlink: false,
    scrollback: 1000,
    allowProposedApi: true,
  })
  const fit = new FitAddon()
  replayTerm.loadAddon(fit)
  replayTerm.open(replayTermEl.value!)
  fit.fit()
}

async function startReplay() {
  if (!replaySession.value) return
  isPlaying.value = true
  replayTerm?.clear()

  let resp: { data: { frames: Array<{t: number; d: string}>; duration_seconds: number } }
  try {
    resp = await api.get(`/ssh/sessions/${replaySession.value.id}/replay`)
  } catch {
    ui.error('Failed to load recording')
    isPlaying.value = false
    return
  }

  const frames = resp.data.frames || []
  const totalDuration = resp.data.duration_seconds || 1
  let idx = 0

  function playNext() {
    if (!isPlaying.value || idx >= frames.length) {
      isPlaying.value = false
      return
    }
    const frame = frames[idx++]
    replayTerm?.write(frame.d)
    replayElapsed.value = frame.t
    replayProgress.value = Math.min(100, (frame.t / totalDuration) * 100)

    const nextDelay = idx < frames.length
      ? Math.max(0, (frames[idx].t - frame.t) * 1000 / replaySpeed.value)
      : 0

    replayTimer = setTimeout(playNext, nextDelay)
  }
  playNext()
}

function pauseReplay() {
  isPlaying.value = false
  if (replayTimer) clearTimeout(replayTimer)
}

function stopReplay() {
  isPlaying.value = false
  if (replayTimer) clearTimeout(replayTimer)
  replayProgress.value = 0
  replayElapsed.value = 0
  replayTerm?.dispose()
  replayTerm = null
}

// Auto-refresh active sessions
let refreshTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  load()
  refreshTimer = setInterval(() => {
    if (sessions.value.some(s => s.status === 'active')) load()
  }, 5000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  stopReplay()
})
</script>

<style scoped>
.sessions-page { display: flex; flex-direction: column; gap: var(--space-5); }
.sessions-header { display: flex; justify-content: space-between; align-items: flex-start; }
.page-title { font-size: 20px; font-weight: 700; }
.page-sub { font-size: 12px; margin-top: 2px; }
.header-actions { display: flex; gap: var(--space-3); align-items: center; }

.filter-tabs { display: flex; border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; }
.filter-tab { padding: var(--space-1) var(--space-4); background: none; border: none; color: var(--text-muted); font-size: 12px; cursor: pointer; transition: all var(--transition); display: flex; gap: var(--space-2); align-items: center; }
.filter-tab.active { background: var(--accent-dim); color: var(--accent); }
.filter-tab:not(:last-child) { border-right: 1px solid var(--border); }
.filter-count { background: var(--bg-subtle); padding: 0 5px; border-radius: var(--radius-full); font-size: 10px; }

.btn-refresh { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); font-size: 16px; cursor: pointer; }

.loading, .empty-state { text-align: center; color: var(--text-muted); padding: var(--space-10); }
.empty-icon { font-size: 48px; color: var(--border-muted); margin-bottom: var(--space-4); }

.sessions-table { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }
.table-header {
  display: grid;
  grid-template-columns: 100px 1fr 160px 160px 90px 160px;
  padding: var(--space-2) var(--space-4);
  background: var(--bg-overlay);
  font-size: 10px; font-weight: 700; color: var(--text-subtle);
  text-transform: uppercase; letter-spacing: 0.08em;
  border-bottom: 1px solid var(--border);
}
.table-row {
  display: grid;
  grid-template-columns: 100px 1fr 160px 160px 90px 160px;
  padding: var(--space-3) var(--space-4);
  align-items: center;
  border-top: 1px solid var(--border);
  font-size: 13px;
  transition: background var(--transition);
}
.table-row:hover { background: var(--bg-overlay); }
.row-active { border-left: 2px solid var(--success); }

.status-pill { font-size: 11px; font-weight: 600; font-family: var(--font-mono); padding: 2px 6px; border-radius: var(--radius-full); }
.pill-active { color: var(--success); background: var(--success-dim); }
.pill-idle   { color: var(--warning); background: var(--warning-dim); }
.pill-closed { color: var(--text-muted); background: var(--bg-subtle); }
.pill-error  { color: var(--error); background: var(--error-dim); }

.row-host { font-weight: 500; }
.row-ip { font-family: var(--font-mono); font-size: 11px; }
.mono { font-family: var(--font-mono); }
.text-muted { color: var(--text-muted); }

.row-actions { display: flex; gap: var(--space-2); }
.action-btn { padding: 2px 10px; border-radius: var(--radius-md); font-size: 11px; font-weight: 600; cursor: pointer; border: 1px solid; transition: all var(--transition); white-space: nowrap; background: none; }
.btn-join { border-color: var(--success); color: var(--success); }
.btn-join:hover { background: var(--success); color: #000; }
.btn-resume { border-color: var(--warning); color: var(--warning); }
.btn-resume:hover { background: var(--warning); color: #000; }
.btn-replay { border-color: var(--accent); color: var(--accent); }
.btn-replay:hover { background: var(--accent); color: #000; }
.btn-kill { border-color: var(--error); color: var(--error); }
.btn-kill:hover { background: var(--error); color: #fff; }

.pagination { display: flex; justify-content: center; align-items: center; gap: var(--space-4); }
.pagination button { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); padding: var(--space-2) var(--space-4); border-radius: var(--radius-md); cursor: pointer; }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }

/* Replay modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.8); display: flex; align-items: center; justify-content: center; z-index: 700; }
.replay-modal { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: var(--radius-xl); width: 90vw; max-width: 1000px; height: 80vh; display: flex; flex-direction: column; box-shadow: var(--shadow-lg); overflow: hidden; }
.replay-header { display: flex; align-items: flex-start; justify-content: space-between; padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--border); flex-shrink: 0; }
.replay-header h3 { font-size: 14px; font-weight: 700; }
.replay-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.replay-controls { display: flex; align-items: center; gap: var(--space-2); }
.ctrl-btn { background: var(--bg-overlay); border: 1px solid var(--border); color: var(--text-muted); padding: var(--space-1) var(--space-3); border-radius: var(--radius-md); cursor: pointer; font-size: 13px; transition: all var(--transition); }
.ctrl-btn:hover { color: var(--text); border-color: var(--border-muted); }
.ctrl-play { border-color: var(--success); color: var(--success); }
.speed-label { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); min-width: 32px; text-align: center; }
.replay-terminal { flex: 1; background: #010409; padding: var(--space-2); overflow: hidden; }
:deep(.xterm) { height: 100%; }
.replay-progress { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-5); border-top: 1px solid var(--border); flex-shrink: 0; }
.progress-bar { flex: 1; height: 4px; background: var(--bg-subtle); border-radius: var(--radius-full); overflow: hidden; }
.progress-fill { height: 100%; background: var(--accent); transition: width 0.1s linear; }
</style>
