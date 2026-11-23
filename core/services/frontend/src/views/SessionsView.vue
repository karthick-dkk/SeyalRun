<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Sessions</h1>
          <p class="sub">Every SSH session brokered by SeyalRun, live and historical, with the commands run in each.</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="loadSessions">↺</button>
        </div>
      </div>

      <section class="card">
        <div class="tabs" role="tablist" style="padding:0 12px">
          <button role="tab" :aria-selected="tab === 'all'" :class="['tab', { on: tab === 'all' }]" @click="tab = 'all'">All sessions <span class="cnt">{{ filteredSessions.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'active'" :class="['tab', { on: tab === 'active' }]" @click="tab = 'active'">Live <span class="cnt" :class="{ hot: activeSessions.length }">{{ activeSessions.length }}</span></button>
        </div>
        <div class="bar">
          <div class="srch" style="flex:1 1 220px;max-width:340px">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
            <label for="s-q" class="vh">Search sessions</label>
            <input id="s-q" v-model="search" type="text" placeholder="Search host or user…" class="in" />
          </div>
          <label for="s-status" class="vh">Status</label>
          <select id="s-status" v-model="statusFilter" class="in" style="width:auto">
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="closed">Closed</option>
            <option value="terminated">Terminated</option>
            <option value="pending">Pending</option>
            <option value="error">Error</option>
          </select>
        </div>

        <div v-if="loading && !sessions.length" class="empty-state">Loading sessions…</div>
        <div v-else-if="error" class="callout" style="--c:var(--bad);margin:16px" role="alert">{{ error }}</div>
        <div v-else-if="tabSessions.length === 0" class="empty-state">
          <b>{{ emptyHeadline }}</b>{{ emptyHint }}
          <div style="margin-top:12px">
            <button v-if="isFiltered" class="btn sm" type="button" @click="clearFilters">Clear filters</button>
            <button v-else class="btn p sm" type="button" @click="openTerminal">Start an SSH session</button>
          </div>
        </div>

        <div v-else class="tw">
          <table class="t">
            <thead>
              <tr><th>Host</th><th>User</th><th>Client IP</th><th>Started</th><th>Duration</th><th>Status</th><th class="r"><span class="vh">Actions</span></th></tr>
            </thead>
            <tbody>
              <template v-for="s in tabSessions" :key="s.id">
                <tr :class="{ sel: expandedId === s.id }">
                  <td><span class="nm">{{ s.host_name || '—' }}</span></td>
                  <td>{{ s.username || '—' }}</td>
                  <td class="mono">{{ s.client_ip || '—' }}</td>
                  <td class="muted">{{ fmtDate(s.started_at) }}</td>
                  <td class="mono">{{ calcDuration(s) }}</td>
                  <td><span class="b" :class="statusKind(s.status)">{{ s.status }}</span></td>
                  <td class="r">
                    <span class="acts" style="justify-content:flex-end;flex-wrap:nowrap;gap:6px">
                      <button class="btn sm" type="button" :aria-expanded="expandedId === s.id" @click="toggleDetail(s.id)" :title="expandedId === s.id ? 'Hide commands' : 'Show commands'">{{ expandedId === s.id ? '▾' : '▸' }} Commands</button>
                      <router-link v-if="recordingMap[s.id]" :to="`/sessions/${recordingMap[s.id]}`" class="btn sm" title="Play recording">▶ Playback</router-link>
                      <router-link v-if="s.status === 'active'" :to="`/sessions/${s.id}/watch`" class="btn sm" title="Watch this live session">Watch</router-link>
                      <button v-if="isAdmin && s.status === 'active'" class="btn sm d" type="button" :disabled="terminating.has(s.id)" @click="terminateSession(s)" title="Terminate session">{{ terminating.has(s.id) ? '…' : 'Terminate' }}</button>
                    </span>
                  </td>
                </tr>
                <tr v-if="expandedId === s.id">
                  <td colspan="7" style="white-space:normal;background:var(--surface2)">
                    <div class="ct" style="margin-bottom:8px">Command history</div>
                    <div v-if="cmdLoading[s.id]" class="muted">Loading…</div>
                    <div v-else-if="!commands[s.id]?.length" class="muted">No commands recorded for this session.</div>
                    <ul v-else class="cmds">
                      <li v-for="cmd in commands[s.id]" :key="cmd.id">
                        <span class="b" :class="cmdKind(cmd.action)">{{ cmd.action }}</span>
                        <code class="mono">{{ cmd.command_text }}</code>
                        <span class="muted">{{ fmtTime(cmd.executed_at) }}</span>
                      </li>
                    </ul>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
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

const isFiltered = computed(() => !!search.value || !!statusFilter.value)
const emptyHeadline = computed(() => {
  if (isFiltered.value) return 'No sessions match those filters'
  return tab.value === 'active' ? 'No one is connected right now' : 'No sessions yet'
})
const emptyHint = computed(() => {
  if (isFiltered.value) return 'Try a different host, user or status.'
  return tab.value === 'active'
    ? 'Live SSH sessions appear here the moment someone connects.'
    : 'Every session SeyalRun brokers is recorded here, with the commands run in it.'
})
function clearFilters() {
  search.value = ''
  statusFilter.value = ''
}
function openTerminal() {
  router.push('/terminal')
}

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

const statusKind = (status: string): string => ({ active: 'ok', closed: 'off', terminated: 'bad', pending: 'pend', error: 'warn' } as Record<string, string>)[status] ?? 'off'
const cmdKind = (a: string): string => ({ allow: 'ok', deny: 'bad', confirm: 'warn' } as Record<string, string>)[a] ?? 'off'

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
.cmds { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 320px; overflow: auto; }
.cmds li { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cmds code { flex: 1 1 200px; min-width: 0; word-break: break-all; font-size: 12.5px; }
</style>
