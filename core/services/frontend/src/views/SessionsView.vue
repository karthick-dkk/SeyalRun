<template>
  <AppShell>
    <div class="sv-wrap">

      <!-- Header -->
      <div class="sv-header">
        <div class="sv-title-row">
          <h1 class="sv-title">Sessions</h1>
          <div class="sv-stats">
            <span class="sv-stat sv-stat--active">
              <span class="sv-stat-dot"></span>
              {{ activeSessions.length }} active
            </span>
            <span class="sv-stat">{{ sessions.length }} total</span>
          </div>
        </div>
        <div class="sv-controls">
          <div class="sv-search">
            <input v-model="search" type="text" placeholder="Search host or user…" class="sv-input" />
          </div>
          <select v-model="statusFilter" class="sv-select">
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="closed">Closed</option>
            <option value="terminated">Terminated</option>
            <option value="pending">Pending</option>
            <option value="error">Error</option>
          </select>
          <button class="sv-refresh" @click="loadSessions" title="Refresh"><svg style="width:14px;height:14px;display:block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></button>
        </div>
      </div>

      <!-- Tab bar -->
      <div class="sv-tabs">
        <button class="sv-tab" :class="{ active: tab === 'all' }" @click="tab = 'all'">
          All Sessions <span class="sv-tab-count">{{ filteredSessions.length }}</span>
        </button>
        <button class="sv-tab" :class="{ active: tab === 'active' }" @click="tab = 'active'">
          <span class="sv-live-dot"></span>Live <span class="sv-tab-count">{{ activeSessions.length }}</span>
        </button>
      </div>

      <!-- Loading / Error -->
      <div v-if="loading && !sessions.length" class="sv-loading">Loading sessions…</div>
      <div v-else-if="error" class="sv-error">{{ error }}</div>

      <!-- Empty state -->
      <div v-else-if="tabSessions.length === 0" class="sv-empty">
        <div class="sv-empty-icon">⬜</div>
        <div class="sv-empty-text">{{ tab === 'active' ? 'No active sessions right now.' : 'No sessions found.' }}</div>
      </div>

      <!-- Sessions table -->
      <div v-else class="sv-table-wrap">
        <table class="sv-table">
          <thead>
            <tr>
              <th class="col-status"></th>
              <th class="col-host">Host</th>
              <th class="col-user">User</th>
              <th class="col-ip">Client IP</th>
              <th class="col-started">Started</th>
              <th class="col-dur">Duration</th>
              <th class="col-status2">Status</th>
              <th class="col-actions"></th>
            </tr>
          </thead>
          <tbody>
            <template v-for="s in tabSessions" :key="s.id">
              <tr class="sv-row" :class="{ 'sv-row--expanded': expandedId === s.id }">
                <td class="col-status">
                  <span class="sv-dot" :class="dotClass(s.status)"></span>
                </td>
                <td class="col-host sv-bold">{{ s.host_name || '—' }}</td>
                <td class="col-user">{{ s.username || '—' }}</td>
                <td class="col-ip sv-mono">{{ s.client_ip || '—' }}</td>
                <td class="col-started sv-mono sv-sm">{{ fmtDate(s.started_at) }}</td>
                <td class="col-dur sv-mono sv-sm">{{ calcDuration(s) }}</td>
                <td class="col-status2">
                  <span class="sv-badge" :class="`sv-badge--${s.status}`">{{ s.status }}</span>
                </td>
                <td class="col-actions sv-actions">
                  <button class="sv-btn sv-btn-detail" @click="toggleDetail(s.id)" :title="expandedId === s.id ? 'Hide commands' : 'Show commands'">
                    {{ expandedId === s.id ? '▾' : '▸' }} Commands
                  </button>
                  <router-link v-if="recordingMap[s.id]" :to="`/sessions/${recordingMap[s.id]}`" class="sv-btn sv-btn-play" title="Play recording">
                    ▶ Playback
                  </router-link>
                  <button
                    v-if="isAdmin && s.status === 'active'"
                    class="sv-btn sv-btn-terminate"
                    :disabled="terminating.has(s.id)"
                    @click="terminateSession(s)"
                    title="Terminate session"
                  >
                    {{ terminating.has(s.id) ? '…' : '✕ Terminate' }}
                  </button>
                </td>
              </tr>
              <!-- Command history panel -->
              <tr v-if="expandedId === s.id" class="sv-cmd-row">
                <td colspan="8" class="sv-cmd-cell">
                  <div class="sv-cmd-panel">
                    <div class="sv-cmd-head">Command History</div>
                    <div v-if="cmdLoading[s.id]" class="sv-cmd-loading">Loading…</div>
                    <div v-else-if="!commands[s.id]?.length" class="sv-cmd-empty">No commands recorded for this session.</div>
                    <div v-else class="sv-cmd-list">
                      <div v-for="cmd in commands[s.id]" :key="cmd.id" class="sv-cmd-item">
                        <span class="sv-cmd-action" :class="`sv-cmd-action--${cmd.action}`">{{ cmd.action }}</span>
                        <code class="sv-cmd-text">{{ cmd.command_text }}</code>
                        <span class="sv-cmd-time">{{ fmtTime(cmd.executed_at) }}</span>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)

interface Session {
  id: string
  user_id: string
  username: string
  host_id: string
  host_name: string
  credential_id: string
  status: string
  client_ip: string
  started_at: string
  ended_at: string | null
}

const sessions = ref<Session[]>([])
const recordingMap = reactive<Record<string, string>>({})  // session_id → recording_id
const loading = ref(true)
const error = ref('')
const search = ref('')
const statusFilter = ref('')
const tab = ref<'all' | 'active'>('all')
const expandedId = ref<string | null>(null)
const commands = reactive<Record<string, any[]>>({})
const cmdLoading = reactive<Record<string, boolean>>({})
const terminating = reactive(new Set<string>())
let refreshTimer: ReturnType<typeof setInterval> | null = null

const activeSessions = computed(() => sessions.value.filter(s => s.status === 'active'))

const filteredSessions = computed(() => {
  let list = sessions.value
  if (statusFilter.value) list = list.filter(s => s.status === statusFilter.value)
  if (search.value) {
    const q = search.value.toLowerCase()
    list = list.filter(s =>
      s.host_name?.toLowerCase().includes(q) ||
      s.username?.toLowerCase().includes(q) ||
      s.client_ip?.includes(q)
    )
  }
  return list
})

const tabSessions = computed(() =>
  tab.value === 'active' ? filteredSessions.value.filter(s => s.status === 'active') : filteredSessions.value
)

async function loadSessions() {
  loading.value = true
  error.value = ''
  try {
    const [sessResp, recResp] = await Promise.all([
      api.get('/ssh/sessions'),
      api.get('/recordings').catch(() => ({ data: [] })),
    ])
    sessions.value = sessResp.data
    for (const r of recResp.data) {
      if (r.session_id) recordingMap[r.session_id] = r.id
    }
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? 'Failed to load sessions'
  } finally {
    loading.value = false
  }
}

async function toggleDetail(sessionId: string) {
  if (expandedId.value === sessionId) {
    expandedId.value = null
    return
  }
  expandedId.value = sessionId
  if (commands[sessionId]) return
  cmdLoading[sessionId] = true
  try {
    const resp = await api.get(`/ssh/sessions/${sessionId}/commands`)
    commands[sessionId] = resp.data
  } catch { commands[sessionId] = [] } finally {
    cmdLoading[sessionId] = false
  }
}

async function terminateSession(s: Session) {
  if (!confirm(`Terminate session for ${s.username} on ${s.host_name}?`)) return
  terminating.add(s.id)
  try {
    await api.delete(`/ssh/sessions/${s.id}`)
    const idx = sessions.value.findIndex(x => x.id === s.id)
    if (idx !== -1) sessions.value[idx] = { ...sessions.value[idx], status: 'terminated', ended_at: new Date().toISOString() }
  } catch { /* show nothing */ } finally {
    terminating.delete(s.id)
  }
}

function dotClass(status: string): string {
  return { active: 'dot-green', closed: 'dot-dim', terminated: 'dot-red', pending: 'dot-blue', error: 'dot-orange' }[status] ?? 'dot-dim'
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString()
}

function calcDuration(s: Session): string {
  if (!s.started_at) return '—'
  const end = s.ended_at ? new Date(s.ended_at).getTime() : Date.now()
  const sec = Math.round((end - new Date(s.started_at).getTime()) / 1000)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  return `${m}m ${sec % 60}s`
}

onMounted(() => {
  loadSessions()
  refreshTimer = setInterval(() => {
    if (activeSessions.value.length > 0 || tab.value === 'active') loadSessions()
  }, 15_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.sv-wrap {
  padding: 24px 28px;
  min-height: 100%;
  background: var(--bg);
  color: var(--text);
}

/* ── Header ─────────────────────────────────────────────────────────────── */
.sv-header { margin-bottom: 16px; }
.sv-title-row { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.sv-title { font-size: 20px; font-weight: 600; margin: 0; }
.sv-stats { display: flex; align-items: center; gap: 12px; }
.sv-stat {
  display: flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--text2);
  background: var(--bg3); border: 1px solid var(--border);
  padding: 2px 8px; border-radius: 10px;
}
.sv-stat--active { border-color: rgba(34,197,94,0.4); color: var(--accent); }
.sv-stat-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent); box-shadow: 0 0 4px var(--accent);
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.sv-controls { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sv-search { flex: 1; min-width: 200px; max-width: 340px; }
.sv-input {
  width: 100%; box-sizing: border-box; padding: 6px 10px;
  background: var(--bg3); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); font-size: 13px; outline: none;
}
.sv-input:focus { border-color: var(--accent2); }
.sv-select {
  padding: 6px 8px; background: var(--bg3); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); font-size: 13px; outline: none; cursor: pointer;
}
.sv-refresh {
  background: none; border: 1px solid var(--border); border-radius: 5px;
  color: var(--text2); padding: 5px 8px; cursor: pointer; font-size: 14px;
}
.sv-refresh { display:flex; align-items:center; justify-content:center; }
.sv-refresh:hover { color: var(--accent2); }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
.sv-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.sv-tab {
  display: flex; align-items: center; gap: 6px; padding: 7px 14px;
  background: none; border: none; border-bottom: 2px solid transparent;
  color: var(--text2); font-size: 13px; cursor: pointer; margin-bottom: -1px;
  transition: color 0.15s, border-color 0.15s;
}
.sv-tab.active { color: var(--text); border-bottom-color: var(--accent2); }
.sv-tab:hover:not(.active) { color: var(--text); }
.sv-tab-count {
  background: var(--border); color: var(--text2); font-size: 11px;
  padding: 0 5px; border-radius: 8px; min-width: 18px; text-align: center;
}
.sv-tab.active .sv-tab-count { background: rgba(59,130,246,0.18); color: var(--accent2); }
.sv-live-dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 4px var(--accent);
}

/* ── States ──────────────────────────────────────────────────────────────── */
.sv-loading { color: var(--text2); padding: 40px; text-align: center; }
.sv-error { color: var(--danger); padding: 16px; background: var(--bg3); border-radius: 6px; }
.sv-empty { text-align: center; padding: 60px 20px; }
.sv-empty-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.3; }
.sv-empty-text { color: var(--text2); font-size: 14px; }

/* ── Table ───────────────────────────────────────────────────────────────── */
.sv-table-wrap { overflow-x: auto; }
.sv-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.sv-table th {
  text-align: left; padding: 8px 12px; background: var(--bg3);
  color: var(--text2); font-weight: 500; font-size: 10px;
  text-transform: uppercase; letter-spacing: 0.06em;
  border-bottom: 1px solid var(--border); white-space: nowrap;
}
.sv-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.sv-row:hover td { background: var(--bg3); }
.sv-row--expanded td { background: var(--bg2); }

.col-status { width: 24px; padding: 0 8px !important; }
.col-host { min-width: 140px; }
.col-user { min-width: 100px; }
.col-ip { min-width: 110px; }
.col-started { min-width: 140px; }
.col-dur { width: 80px; }
.col-status2 { width: 90px; }
.col-actions { min-width: 200px; }

.sv-bold { font-weight: 500; }
.sv-mono { font-family: monospace; color: var(--text2); }
.sv-sm { font-size: 12px; }
.sv-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

/* ── Dots ────────────────────────────────────────────────────────────────── */
.sv-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot-green  { background: var(--accent); box-shadow: 0 0 4px var(--accent); animation: pulse 1.5s ease-in-out infinite; }
.dot-red    { background: var(--danger); }
.dot-blue   { background: var(--accent2); }
.dot-orange { background: var(--warn); }
.dot-dim    { background: var(--border); }

/* ── Badges ──────────────────────────────────────────────────────────────── */
.sv-badge {
  display: inline-block; padding: 2px 7px; border-radius: 10px;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
}
.sv-badge--active     { background: rgba(34,197,94,0.18); color: var(--accent); }
.sv-badge--closed     { background: var(--bg3); color: var(--text2); }
.sv-badge--terminated { background: rgba(239,68,68,0.18); color: var(--danger); }
.sv-badge--pending    { background: rgba(59,130,246,0.18); color: var(--accent2); }
.sv-badge--error      { background: rgba(210,153,34,0.2); color: var(--warn); }

/* ── Action buttons ──────────────────────────────────────────────────────── */
.sv-btn {
  padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 500;
  border: 1px solid var(--border); background: var(--bg3); color: var(--text2);
  cursor: pointer; text-decoration: none; display: inline-flex; align-items: center;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
}
.sv-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.sv-btn-detail:hover    { border-color: var(--text2); color: var(--text); }
.sv-btn-play            { color: var(--accent2); border-color: rgba(59,130,246,0.35); }
.sv-btn-play:hover      { border-color: var(--accent2); }
.sv-btn-terminate       { color: var(--danger); border-color: rgba(239,68,68,0.35); }
.sv-btn-terminate:hover { border-color: var(--danger); background: rgba(239,68,68,0.12); }

/* ── Command history panel ───────────────────────────────────────────────── */
.sv-cmd-row td { padding: 0; border-bottom: 1px solid var(--border); }
.sv-cmd-cell { background: var(--bg2); }
.sv-cmd-panel { padding: 12px 20px 14px 36px; }
.sv-cmd-head {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text2); font-weight: 600; margin-bottom: 8px;
}
.sv-cmd-loading, .sv-cmd-empty { font-size: 12px; color: var(--text2); }
.sv-cmd-list { display: flex; flex-direction: column; gap: 3px; max-height: 260px; overflow-y: auto; }
.sv-cmd-item {
  display: flex; align-items: baseline; gap: 10px;
  padding: 4px 8px; border-radius: 4px; background: var(--bg3);
  font-size: 12px;
}
.sv-cmd-item:hover { background: var(--border); }
.sv-cmd-action {
  flex-shrink: 0; padding: 1px 5px; border-radius: 3px; font-size: 10px;
  font-weight: 600; text-transform: uppercase; min-width: 44px; text-align: center;
}
.sv-cmd-action--logged    { background: var(--bg2); color: var(--text2); }
.sv-cmd-action--allow     { background: rgba(34,197,94,0.18); color: var(--accent); }
.sv-cmd-action--deny      { background: rgba(239,68,68,0.18); color: var(--danger); }
.sv-cmd-action--confirm   { background: rgba(210,153,34,0.2); color: var(--warn); }
.sv-cmd-text {
  flex: 1; font-family: 'Menlo', 'Monaco', monospace; color: var(--text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sv-cmd-time {
  flex-shrink: 0; font-family: monospace; font-size: 10px; color: var(--text2);
}
</style>
