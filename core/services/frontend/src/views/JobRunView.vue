<template>
  <AppShell>
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Job Run</div>
          <div class="page-subtitle" v-if="run">{{ run.job_template_name || run.job_template_id }}</div>
        </div>
        <div v-if="run" style="display:flex;gap:12px;align-items:center">
          <span :class="statusClass(run.status)" style="font-size:14px">{{ run.status }}</span>
          <span v-if="run.params?._dry_run" class="badge" style="font-size:11px;color:var(--warn);border-color:rgba(210,153,34,0.4);background:rgba(210,153,34,0.08)" title="Ran with --check --diff — no changes were applied">DRY RUN</span>
          <template v-if="run.status === 'pending_approval'">
            <button v-if="canApprove" class="btn btn-sm" style="color:var(--accent)" :disabled="approving" @click="approveRun">Approve</button>
            <button v-if="canApprove" class="btn btn-sm" style="color:var(--danger)" :disabled="approving" @click="rejectRun">Reject</button>
            <span v-if="!canApprove" style="font-size:12px;color:var(--text2)">Waiting for approval</span>
          </template>
          <button v-else-if="['pending','running'].includes(run.status)" class="btn btn-sm" style="color:var(--danger)" @click="cancelRun">Cancel</button>
          <button v-else class="btn btn-sm" :disabled="rerunning" @click="rerun">{{ rerunning ? 'Re-running…' : '↻ Re-run' }}</button>
          <button class="btn btn-sm" @click="router.push('/jobs')">← Job Runs</button>
        </div>
      </div>

      <div v-if="run" class="card" style="margin-bottom:16px">
        <div class="card-header" style="font-size:13px">Run Details</div>
        <div style="padding:12px 16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;font-size:13px">
          <div><span style="color:var(--text2)">Template:</span> {{ run.job_template_name || run.job_template_id }}</div>
          <div><span style="color:var(--text2)">Action:</span> {{ run.action_type || '—' }}</div>
          <div><span style="color:var(--text2)">Triggered by:</span> {{ triggeredByLabel }}</div>
          <div v-if="isAccountOp" style="grid-column:1/-1"><span style="color:var(--text2)">Account (subject):</span> {{ subjectLabel }}</div>
          <div><span style="color:var(--text2)">Connection login:</span> {{ credentialLabel }}</div>
          <div style="grid-column:1/-1"><span style="color:var(--text2)">Hosts:</span> {{ hostsLabel }}</div>
          <div><span style="color:var(--text2)">Run ID:</span> <code style="font-size:11px">{{ run.id }}</code></div>
          <div><span style="color:var(--text2)">Started:</span> {{ run.started_at ? new Date(run.started_at).toLocaleString() : '—' }}</div>
          <div><span style="color:var(--text2)">Duration:</span> {{ durationLabel }}</div>
          <div v-if="run.exit_code != null"><span style="color:var(--text2)">Exit code:</span> {{ run.exit_code }}</div>
          <div v-if="run.status === 'rejected' && run.params?._rejection_reason" style="grid-column:1/-1"><span style="color:var(--text2)">Rejection reason:</span> {{ run.params._rejection_reason }}</div>
        </div>
      </div>

      <div v-if="run?.action_type === 'chain'" class="card" style="margin-bottom:16px">
        <div class="card-header" style="font-size:13px">Chain Steps</div>
        <div v-if="!chainSteps.length" style="padding:14px 16px;font-size:12px;color:var(--text2)">Steps haven't started yet…</div>
        <table v-else class="table" style="margin:0">
          <thead><tr><th style="width:36px">#</th><th>Template</th><th>Status</th><th>Duration</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in chainSteps" :key="s.id">
              <td>{{ i + 1 }}</td>
              <td>{{ s.job_template_name || s.job_template_id }}</td>
              <td><span :class="statusClass(s.status)">{{ s.status }}</span></td>
              <td style="color:var(--text2);font-size:12px">{{ stepDuration(s) }}</td>
              <td><router-link :to="`/jobs/${s.id}`" class="btn btn-sm">View →</router-link></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-header" style="font-size:13px">
          Output Log
          <div style="display:flex;align-items:center;gap:14px">
            <button class="btn btn-sm" :disabled="!lines.length" @click="copyOutput" :title="copyError || (copied ? 'Copied!' : 'Copy output')">
              {{ copied ? '✓ Copied' : (copyError ? '✕ Copy failed' : '⧉ Copy') }}
            </button>
            <label style="display:flex;align-items:center;gap:6px;font-weight:400;font-size:12px;cursor:pointer">
              <input type="checkbox" v-model="autoScroll" />
              Auto-scroll
            </label>
          </div>
        </div>
        <div ref="logEl" style="background:#0d1117;color:#c9d1d9;font-family:monospace;font-size:12px;padding:16px;min-height:320px;max-height:60vh;overflow-y:auto;white-space:pre-wrap;border-radius:0 0 8px 8px">
          <span v-if="!lines.length && !run" style="color:#8b949e">Loading…</span>
          <span v-else-if="!lines.length" style="color:#8b949e">(no output yet)</span>
          <span v-for="(line, i) in lines" :key="i">{{ line }}<br /></span>
          <span v-if="['pending','running'].includes(run?.status ?? '')" style="color:#58a6ff">● live…</span>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api, { wsUrl } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const runId = computed(() => route.params.id as string)
const run = ref<any>(null)
const lines = ref<string[]>([])
const autoScroll = ref(true)
const logEl = ref<HTMLElement | null>(null)
let ws: WebSocket | null = null

// Best-effort name lookups (admin-gated endpoints degrade to ids for non-admins).
const hostMap = ref<Record<string, string>>({})
const credMap = ref<Record<string, any>>({})
const userMap = ref<Record<string, string>>({})

async function loadMaps() {
  const [hosts, creds, users] = await Promise.all([
    api.get('/hosts').then(r => r.data).catch(() => []),
    api.get('/credentials').then(r => r.data).catch(() => []),
    api.get('/users').then(r => r.data).catch(() => []),
  ])
  hostMap.value = Object.fromEntries(hosts.map((h: any) => [h.id, h.name]))
  credMap.value = Object.fromEntries(creds.map((c: any) => [c.id, c]))
  userMap.value = Object.fromEntries(users.map((u: any) => [u.id, u.username || u.name || u.id]))
}

// The subject account = the user actually created / managed on the hosts (account ops).
const ACCOUNT_OPS = ['account_push', 'rotate_secret', 'disable_account', 'remove_account']
const isAccountOp = computed(() => ACCOUNT_OPS.includes(run.value?.action_type))
const subjectLabel = computed(() => {
  const id = run.value?.subject_credential_id
  if (!id) return '— not set —'
  const c = credMap.value[id]
  if (!c) return id
  return c.username ? `${c.name} → user “${c.username}”` : c.name
})

const triggeredByLabel = computed(() => {
  const r = run.value
  if (!r) return '—'
  if (r.triggered_by_kind === 'user' && r.triggered_by_user_id) {
    return '👤 ' + (userMap.value[r.triggered_by_user_id] || r.triggered_by_user_id)
  }
  return r.triggered_by || '—'
})
const hostsLabel = computed(() => {
  const ids: string[] = run.value?.target_host_ids || []
  if (!ids.length) return '—'
  return ids.map(id => hostMap.value[id] || id).join(', ')
})
const credentialLabel = computed(() => {
  const r = run.value
  if (!r) return '—'
  if (Object.keys(r.host_credentials || {}).length) return 'Per-host credentials'
  if (!r.credential_id) return "Default — host's push account"
  return credMap.value[r.credential_id]?.name || r.credential_id
})

async function fetchRun() {
  run.value = (await api.get(`/job-runs/${runId.value}`)).data
  lines.value = (run.value.output_lines || []) as string[]
  if (run.value.action_type === 'chain') fetchChainSteps()
}

const chainSteps = ref<any[]>([])
let chainPoll: ReturnType<typeof setInterval> | null = null
async function fetchChainSteps() {
  const { data } = await api.get('/job-runs', { params: { parent_run_id: runId.value } })
  // Oldest first — the order steps actually ran in.
  chainSteps.value = [...data].reverse()
}
function stepDuration(s: any): string {
  if (!s.started_at) return '—'
  const end = s.ended_at ? new Date(s.ended_at) : new Date()
  const secs = Math.round((end.getTime() - new Date(s.started_at).getTime()) / 1000)
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
}

function openWs() {
  if (ws) return
  ws = new WebSocket(wsUrl(`jobs/${runId.value}/log`))
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'line') {
        lines.value.push(msg.line)
        if (autoScroll.value) scrollToBottom()
      } else if (msg.type === 'done') {
        run.value = { ...run.value, status: msg.status, exit_code: msg.exit_code }
        ws?.close()
        ws = null
        fetchRun()
      }
    } catch { /* non-JSON frame */ }
  }
  ws.onerror = () => { ws = null }
}

function scrollToBottom() {
  nextTick(() => {
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  })
}

watch(lines, () => {
  if (autoScroll.value) scrollToBottom()
})

async function loadRun() {
  ws?.close()
  ws = null
  if (chainPoll) { clearInterval(chainPoll); chainPoll = null }
  run.value = null
  lines.value = []
  chainSteps.value = []
  await fetchRun()
  if (['pending', 'running'].includes(run.value?.status)) {
    lines.value = []   // the WS replays the full buffer — avoid double-printing
    openWs()
  }
  // Chain steps progress independently of the parent's own output_lines/WS —
  // poll them directly so the step table updates live while the chain runs.
  if (run.value?.action_type === 'chain') {
    chainPoll = setInterval(async () => {
      await fetchChainSteps()
      if (!['pending', 'running'].includes(run.value?.status)) {
        clearInterval(chainPoll!); chainPoll = null
      }
    }, 2000)
  }
}

onMounted(() => {
  loadMaps()
  loadRun()
})

// Vue Router reuses this component instance across /jobs/:id -> /jobs/:otherId
// navigations (same route record), so onMounted only fires once — without this,
// switching runs (e.g. after Re-run) would keep showing the old run's data.
watch(() => route.params.id, (_new, old) => {
  if (old !== undefined) loadRun()
})

onUnmounted(() => {
  ws?.close()
  if (chainPoll) clearInterval(chainPoll)
})

async function cancelRun() {
  await api.delete(`/job-runs/${runId.value}`)
  await fetchRun()
}

// Client-side hint only, matching the run's own approver_role (defaults to "admin"
// tier = admin-or-above) — the backend endpoint re-checks this and is the real gate.
const canApprove = computed(() => {
  const required = run.value?.approver_role || 'admin'
  const roles: string[] = auth.roles || []
  if (required === 'superadmin') return roles.includes('superadmin')
  return roles.includes('admin') || roles.includes('superadmin')
})

const approving = ref(false)
async function approveRun() {
  approving.value = true
  try {
    await api.post(`/job-runs/${runId.value}/approve`)
    await fetchRun()
    if (['pending', 'running'].includes(run.value?.status)) {
      lines.value = []
      openWs()
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Approve failed')
  } finally {
    approving.value = false
  }
}
async function rejectRun() {
  const reason = prompt('Reason for rejecting this run (optional):') || ''
  approving.value = true
  try {
    await api.post(`/job-runs/${runId.value}/reject`, { reason })
    await fetchRun()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Reject failed')
  } finally {
    approving.value = false
  }
}

const rerunning = ref(false)
async function rerun() {
  rerunning.value = true
  try {
    const { data } = await api.post(`/job-runs/${runId.value}/rerun`)
    if (data?.run_id) {
      router.push(`/jobs/${data.run_id}`)
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Re-run failed')
  } finally {
    rerunning.value = false
  }
}

const copied = ref(false)
const copyError = ref('')

// Fallback for when navigator.clipboard is missing or rejects (blocked permission,
// non-standard browser context, etc.) — a hidden textarea + execCommand('copy') works
// far more broadly, at the cost of being a deprecated API. Returns whether it worked.
function legacyCopy(text: string): boolean {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.top = '-1000px'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  document.body.removeChild(ta)
  return ok
}

async function copyOutput() {
  copyError.value = ''
  const text = lines.value.join('\n')
  try {
    if (!navigator.clipboard) throw new Error('Clipboard API unavailable in this browser context')
    await navigator.clipboard.writeText(text)
  } catch (e: any) {
    if (!legacyCopy(text)) {
      copyError.value = e?.message || 'Copy failed — your browser blocked clipboard access'
      setTimeout(() => { copyError.value = '' }, 3000)
      return
    }
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}

function statusClass(s: string) {
  if (s === 'success') return 'badge badge-green'
  if (s === 'failed' || s === 'error' || s === 'rejected') return 'badge badge-red'
  if (s === 'running') return 'badge badge-blue'
  if (s === 'pending_approval') return 'badge badge-yellow'
  return 'badge badge-gray'
}

const durationLabel = computed(() => {
  if (!run.value?.started_at) return '—'
  const end = run.value.ended_at ? new Date(run.value.ended_at) : new Date()
  const secs = Math.round((end.getTime() - new Date(run.value.started_at).getTime()) / 1000)
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
})
</script>
