<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Approvals</h1>
          <p class="sub">Requests that need a second admin. You can't approve your own.</p>
        </div>
        <div class="acts">
          <span class="seg" role="group" aria-label="Filter">
            <button type="button" :class="{ on: scope === 'me' }" @click="scope = 'me'">Waiting on me</button>
            <button type="button" :class="{ on: scope === 'mine' }" @click="scope = 'mine'">I requested</button>
            <button type="button" :class="{ on: scope === 'all' }" @click="scope = 'all'">All pending</button>
          </span>
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="load">↺</button>
        </div>
      </div>

      <section class="card">
        <div class="tabs" role="tablist" style="padding:0 12px">
          <button role="tab" :aria-selected="tab === 'runs'" :class="['tab', { on: tab === 'runs' }]" @click="tab = 'runs'">Job runs <span class="cnt" :class="{ hot: runList.length }">{{ runList.length }}</span></button>
          <button v-if="auth.isAdmin" role="tab" :aria-selected="tab === 'rules'" :class="['tab', { on: tab === 'rules' }]" @click="tab = 'rules'">Access rules <span class="cnt" :class="{ hot: ruleList.length }">{{ ruleList.length }}</span></button>
        </div>
        <div v-if="error" class="callout" style="--c:var(--bad);margin:16px" role="alert">{{ error }}</div>

        <template v-if="tab === 'runs'">
          <div v-if="!runList.length" class="empty-state"><b>Nothing waiting</b>No job runs are pending approval here.</div>
          <ul v-else class="al">
            <li v-for="r in runList" :key="r.id">
              <span class="av">{{ ini(userName(r.triggered_by_user_id)) }}</span>
              <div class="al-b">
                <div class="al-h"><b>{{ r.job_template_name || 'Playbook run' }}</b><span class="muted" style="font-size:12.5px">{{ ago(r.started_at) }}</span></div>
                <span class="muted" style="font-size:13px">{{ userName(r.triggered_by_user_id) }} · {{ (r.target_host_ids || []).length }} host{{ (r.target_host_ids || []).length === 1 ? '' : 's' }} · {{ r.action_type || 'playbook' }}</span>
                <div class="acts" style="gap:6px">
                  <span class="b pend">Awaiting approval</span>
                  <router-link class="btn sm" :to="`/jobs/${r.id}`">Review</router-link>
                  <template v-if="canApproveRun(r)">
                    <button class="btn sm p" :disabled="busy === r.id" @click="approveRun(r)">Approve</button>
                    <button class="btn sm d" :disabled="busy === r.id" @click="rejectRun(r)">Reject</button>
                  </template>
                  <span v-else-if="isOwn(r.triggered_by_user_id)" class="muted" style="font-size:12.5px">You requested this · waiting for another approver</span>
                </div>
              </div>
            </li>
          </ul>
        </template>

        <template v-else>
          <div v-if="!ruleList.length" class="empty-state"><b>Nothing waiting</b>No access rules are pending approval here.</div>
          <ul v-else class="al">
            <li v-for="a in ruleList" :key="a.id">
              <span class="av">{{ ini(userName(a.requested_by)) }}</span>
              <div class="al-b">
                <div class="al-h"><b>{{ a.name }}</b><span class="muted" style="font-size:12.5px">{{ ago(a.created_at) }}</span></div>
                <template v-if="a.request_kind === 'user_request'">
                  <!-- A user's own access request: everything the approver needs to judge it. -->
                  <dl class="req">
                    <dt>Requester</dt><dd>{{ userName(a.requested_by) }}</dd>
                    <dt>Host</dt><dd>{{ hostName(a.host_id) }}</dd>
                    <dt>Account</dt><dd>{{ credName(a.credential_id) }}</dd>
                    <dt>Duration</dt><dd>{{ fmtDuration(a.request_duration_minutes) }} from approval · {{ (a.actions || []).join(', ') }}</dd>
                    <dt>Reason</dt><dd class="req-reason">{{ a.reason || '—' }}</dd>
                  </dl>
                </template>
                <span v-else class="muted" style="font-size:13px">{{ userName(a.requested_by) }} · {{ (a.actions || []).join(', ') }}</span>
                <div class="acts" style="gap:6px">
                  <span class="b pend">Awaiting approval</span>
                  <router-link class="btn sm" to="/admin/authorizations">Open in Access rules</router-link>
                  <template v-if="!isOwn(a.requested_by)">
                    <button class="btn sm p" :disabled="busy === a.id" @click="approveRule(a)">Approve</button>
                    <button class="btn sm d" :disabled="busy === a.id" @click="rejectRule(a)">Reject</button>
                  </template>
                  <span v-else class="muted" style="font-size:12.5px">You requested this · a different admin must approve</span>
                </div>
              </div>
            </li>
          </ul>
        </template>
      </section>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { refreshApprovals } from '@/composables/useApprovals'

const auth = useAuthStore()
const tab = ref<'runs' | 'rules'>('runs')
const scope = ref<'me' | 'mine' | 'all'>('me')
const runs = ref<any[]>([])
const rules = ref<any[]>([])
const users = ref<Record<string, string>>({})
const hostNames = ref<Record<string, string>>({})
const credNames = ref<Record<string, string>>({})
const error = ref('')
const busy = ref('')

const me = computed(() => auth.user?.id || '')
const isOwn = (id?: string | null) => !!id && id === me.value
function canApproveRun(r: any) {
  const req = r.approver_role || 'admin'
  const ok = req === 'superadmin' ? auth.roles.includes('superadmin') : auth.isAdmin
  return ok && !isOwn(r.triggered_by_user_id)
}
const pick = (list: any[], who: (x: any) => string | null | undefined, canAct: (x: any) => boolean) =>
  list.filter((x) => scope.value === 'all' || (scope.value === 'mine' ? isOwn(who(x)) : canAct(x)))
const runList = computed(() => pick(runs.value, (r) => r.triggered_by_user_id, canApproveRun))
const ruleList = computed(() => pick(rules.value, (a) => a.requested_by, (a) => auth.isAdmin && !isOwn(a.requested_by)))

const userName = (id?: string | null) => (id && users.value[id]) || (isOwn(id) ? (auth.user?.username || 'you') : 'unknown')
const ini = (n: string) => (n || '?').slice(0, 2).toUpperCase()
const hostName = (id?: string | null) => (id && hostNames.value[id]) || id || '—'
const credName = (id?: string | null) => (id && credNames.value[id]) || id || '—'
function fmtDuration(m?: number | null) {
  if (!m) return '—'
  if (m % 1440 === 0) return `${m / 1440} day${m === 1440 ? '' : 's'}`
  if (m % 60 === 0) return `${m / 60} hour${m === 60 ? '' : 's'}`
  return `${m} min`
}
function ago(iso?: string | null) {
  if (!iso) return ''
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

async function load() {
  error.value = ''
  try { runs.value = (await api.get('/job-runs', { params: { status: 'pending_approval' } })).data || [] }
  catch (e: any) { runs.value = []; error.value = e?.response?.data?.detail || 'Could not load job runs.' }
  if (auth.isAdmin) {
    try { rules.value = ((await api.get('/authorizations')).data || []).filter((a: any) => a.status === 'pending_approval') } catch { rules.value = [] }
    try { users.value = Object.fromEntries(((await api.get('/users')).data || []).map((u: any) => [u.id, u.username])) } catch { /* names fall back */ }
    if (rules.value.some((a: any) => a.request_kind === 'user_request')) {
      try { hostNames.value = Object.fromEntries(((await api.get('/hosts')).data || []).map((h: any) => [h.id, h.name])) } catch { /* ids fall back */ }
      try {
        credNames.value = Object.fromEntries(((await api.get('/credentials')).data || [])
          .map((c: any) => [c.id, c.name && c.name !== c.username ? `${c.username} (${c.name})` : c.username]))
      } catch { /* ids fall back */ }
    }
  }
  refreshApprovals(auth.isAdmin)
}
async function act(id: string, fn: () => Promise<unknown>, fail: string) {
  busy.value = id; error.value = ''
  try { await fn(); await load() } catch (e: any) { error.value = e?.response?.data?.detail || fail } finally { busy.value = '' }
}
const approveRun = (r: any) => act(r.id, () => api.post(`/job-runs/${r.id}/approve`), 'Approve failed')
function rejectRun(r: any) {
  const reason = prompt('Reason for rejecting this run (optional):')
  if (reason === null) return
  act(r.id, () => api.post(`/job-runs/${r.id}/reject`, { reason }), 'Reject failed')
}
const approveRule = (a: any) => act(a.id, () => api.post(`/authorizations/${a.id}/approve`), 'Approve failed')
const rejectRule = (a: any) => act(a.id, () => api.post(`/authorizations/${a.id}/reject`), 'Reject failed')
onMounted(load)
</script>

<style scoped>
.al { list-style: none; margin: 0; padding: 0; }
.al li { padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex; gap: 12px; }
.al li:last-child { border-bottom: 0; }
.al-b { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.al-h { display: flex; justify-content: space-between; gap: 8px; }
.req { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 2px 0 4px; font-size: 13px; }
.req dt { color: var(--text2); }
.req dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.req-reason { white-space: pre-wrap; }
</style>
