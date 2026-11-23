<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Run details</h1>
          <p class="sub" v-if="run">{{ run.job_template_name || run.job_template_id }}</p>
        </div>
        <div v-if="run" class="acts">
          <span :class="statusClass(run.status)">{{ run.status }}</span>
          <span v-if="run.params?._dry_run" class="b warn" title="Ran with --check --diff — no changes were applied">Dry run</span>
          <template v-if="run.status === 'pending_approval'">
            <button v-if="canApprove && !isOwnRun" class="btn p" type="button" :disabled="approving" @click="approveRun">Approve</button>
            <button v-if="canApprove" class="btn d" type="button" :disabled="approving" @click="rejectRun">{{ isOwnRun ? 'Withdraw' : 'Reject' }}</button>
            <span class="muted" style="font-size:12.5px" v-if="isOwnRun">Waiting for another approver — you requested this run</span>
            <span class="muted" style="font-size:12.5px" v-else-if="!canApprove">Waiting for approval</span>
          </template>
          <button v-else-if="['pending','running'].includes(run.status)" class="btn d" type="button" @click="cancelRun">Cancel</button>
          <button v-else class="btn" type="button" :disabled="rerunning" @click="rerun">{{ rerunning ? 'Re-running…' : '↻ Re-run' }}</button>
          <button class="btn" type="button" @click="router.push('/jobs')">← Job runs</button>
        </div>
      </div>

      <div class="stack">
        <section v-if="run" class="card">
          <div class="ch"><h2 class="ct">Run details</h2></div>
          <div class="cb">
            <dl class="kv">
              <dt>Template</dt><dd>{{ run.job_template_name || run.job_template_id }}</dd>
              <dt>Action</dt><dd>{{ run.action_type || '—' }}</dd>
              <dt>Triggered by</dt><dd>{{ triggeredByLabel }}</dd>
              <template v-if="isAccountOp"><dt>Account (subject)</dt><dd>{{ subjectLabel }}</dd></template>
              <dt>Connection login</dt><dd>{{ credentialLabel }}</dd>
              <dt>Hosts</dt><dd>{{ hostsLabel }}</dd>
              <dt>Run ID</dt><dd><code class="mono">{{ run.id }}</code></dd>
              <dt>Started</dt><dd>{{ run.started_at ? new Date(run.started_at).toLocaleString() : '—' }}</dd>
              <dt>Duration</dt><dd>{{ durationLabel }}</dd>
              <template v-if="run.exit_code != null"><dt>Exit code</dt><dd class="mono">{{ run.exit_code }}</dd></template>
              <template v-if="run.status === 'rejected' && run.params?._rejection_reason"><dt>Rejection reason</dt><dd>{{ run.params._rejection_reason }}</dd></template>
            </dl>
          </div>
        </section>

        <section v-if="run?.action_type === 'chain'" class="card">
          <div class="ch"><h2 class="ct">Chain steps</h2></div>
          <div v-if="!chainSteps.length" class="empty-state">Steps haven't started yet…</div>
          <div v-else class="tw">
            <table class="t">
              <thead><tr><th style="width:36px">#</th><th>Template</th><th>Status</th><th>Duration</th><th class="r"><span class="vh">Open</span></th></tr></thead>
              <tbody>
                <tr v-for="(s, i) in chainSteps" :key="s.id">
                  <td class="num">{{ i + 1 }}</td>
                  <td><span class="nm">{{ s.job_template_name || s.job_template_id }}</span></td>
                  <td><span :class="statusClass(s.status)">{{ s.status }}</span></td>
                  <td class="muted">{{ stepDuration(s) }}</td>
                  <td class="r"><router-link :to="`/jobs/${s.id}`" class="btn sm">View →</router-link></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <div class="ch">
            <h2 class="ct">Output log</h2>
            <div class="acts">
              <button class="btn sm" type="button" :disabled="!lines.length" @click="copyOutput" :title="copyError || (copied ? 'Copied!' : 'Copy output')">
                {{ copied ? '✓ Copied' : (copyError ? '✕ Copy failed' : '⧉ Copy') }}
              </button>
              <label class="row" style="gap:6px;font-size:12.5px;cursor:pointer">
                <input type="checkbox" class="chk" v-model="autoScroll" />
                Auto-scroll
              </label>
            </div>
          </div>
          <div class="cb">
            <div ref="logEl" class="console" style="min-height:320px;max-height:60vh;overflow-y:auto;white-space:pre-wrap" role="log" aria-label="Run output">
              <span v-if="!lines.length && !run" style="color:#8b949e">Loading…</span>
              <span v-else-if="!lines.length" style="color:#8b949e">(no output yet)</span>
              <span v-for="(line, i) in lines" :key="i">{{ line }}<br /></span>
              <span v-if="['pending','running'].includes(run?.status ?? '')" style="color:#6FA8FF">● live…</span>
            </div>
          </div>
        </section>
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
    return (userMap.value[r.triggered_by_user_id] || r.triggered_by_user_id)
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

// The requester may not approve their own run (separation of duties); the backend
// enforces it, this only avoids offering a button that can only fail.
const isOwnRun = computed(() =>
  !!auth.user?.id && run.value?.triggered_by_user_id === auth.user.id)

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
  if (s === 'success') return 'b ok'
  if (s === 'failed' || s === 'error' || s === 'rejected') return 'b bad'
  if (s === 'running') return 'b info'
  if (s === 'pending_approval') return 'b pend'
  return 'b off'
}

const durationLabel = computed(() => {
  if (!run.value?.started_at) return '—'
  const end = run.value.ended_at ? new Date(run.value.ended_at) : new Date()
  const secs = Math.round((end.getTime() - new Date(run.value.started_at).getTime()) / 1000)
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
})
</script>
