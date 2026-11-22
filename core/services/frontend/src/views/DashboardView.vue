<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">{{ greeting }}, {{ firstName }}</h1>
          <p class="sub">{{ subline }}</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" title="Refresh" aria-label="Refresh" @click="load">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></svg>
          </button>
        </div>
      </div>

      <div class="stack">
        <!-- ── Needs attention ─────────────────────────────────────────────── -->
        <section aria-label="Needs attention" class="tiles" style="--min:230px">
          <router-link v-for="c in cards" :key="c.t" :to="c.to" class="card att" :style="{ borderColor: c.kind === 'pend' ? 'color-mix(in srgb, var(--pend) 45%, var(--border))' : 'var(--border)' }">
            <span class="b nd" :class="c.kind" style="width:34px;height:34px;padding:0;justify-content:center;border-radius:8px">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="c.icon" /></svg>
            </span>
            <span><b>{{ c.t }}</b><span class="att-s">{{ c.s }}</span></span>
          </router-link>
        </section>

        <!-- ── Stat tiles ──────────────────────────────────────────────────── -->
        <section class="tiles" style="--min:220px">
          <router-link v-for="t in stats" :key="t.l" :to="t.to" class="card stat statlink">
            <span class="l">{{ t.l }}</span>
            <span class="v">{{ t.v }}</span>
            <span class="statd">{{ t.d }}</span>
            <span v-if="t.bar" class="hbar" role="img" :aria-label="t.bar.label">
              <span :title="t.bar.okT" :style="{ flex: t.bar.ok, background: '#86EFAC' }" />
              <span :title="t.bar.badT" :style="{ flex: t.bar.bad, background: '#FCA5A5' }" />
            </span>
            <svg v-else-if="t.spark" class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">
              <path :d="t.spark.area" class="spark-area" />
              <path :d="t.spark.line" class="spark-line" />
            </svg>
          </router-link>
        </section>

        <!-- ── Activity chart ──────────────────────────────────────────────── -->
        <section class="card">
          <div class="ch">
            <h2 class="ct">{{ isAdmin ? 'Activity' : 'Your activity' }}, {{ rangeTitle }}</h2>
            <div class="acts" style="font-size:12.5px;color:var(--muted);gap:14px">
              <span class="seg" role="radiogroup" aria-label="Activity period">
                <span v-for="r in RANGES" :key="r.id" :class="{ on: range === r.id }" role="radio" :aria-checked="range === r.id" tabindex="0" style="cursor:pointer;height:28px"
                  @click="range = r.id" @keydown.enter.prevent="range = r.id" @keydown.space.prevent="range = r.id">{{ r.label }}</span>
              </span>
              <span class="row" style="gap:6px"><span class="sw" style="background:#38BDF8" />Job runs</span>
              <span class="row" style="gap:6px"><span class="sw" style="background:#A5D8FF" />SSH sessions</span>
            </div>
          </div>
          <div class="cb" style="display:flex;gap:12px">
            <div class="yaxis num" aria-hidden="true"><span v-for="y in chart.ticks" :key="y">{{ y }}</span></div>
            <div style="flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:6px">
              <div class="plot" :style="{ gridTemplateColumns: `repeat(${chart.n}, minmax(0, 1fr))`, gap: chart.gap + 'px' }">
                <div v-for="d in chart.days" :key="d.when" class="pcol" :title="d.label">
                  <span class="pbar" :style="{ background: '#38BDF8', height: d.jh + 'px' }" />
                  <span class="pbar" :style="{ background: '#A5D8FF', height: d.sh + 'px' }" />
                </div>
              </div>
              <div class="xaxis" :style="{ gridTemplateColumns: `repeat(${chart.n}, minmax(0, 1fr))`, gap: chart.gap + 'px' }">
                <span v-for="d in chart.days" :key="d.when">{{ d.tick }}</span>
              </div>
            </div>
          </div>
          <div v-if="chart.note" class="foot">{{ chart.note }}</div>
        </section>

        <!-- ── Admin tables ────────────────────────────────────────────────── -->
        <template v-if="isAdmin">
          <section class="grid2">
            <div class="card">
              <div class="ch"><h2 class="ct">Recent runs</h2><router-link class="lk" to="/automation?tab=runs" style="font-size:13px">All runs</router-link></div>
              <div class="tw"><table class="t">
                <thead><tr><th>Status</th><th>Playbook</th><th>Started by</th><th class="r">When</th></tr></thead>
                <tbody>
                  <tr v-for="j in (m?.recent_jobs || [])" :key="j.id">
                    <td><span class="b" :class="runKind(j.status)">{{ runLabel(j.status) }}</span></td>
                    <td><router-link class="nm" :to="`/jobs/${j.id}`">{{ shortName(j.playbook) }}</router-link></td>
                    <td>{{ jobActor(j.triggered_by) }}</td>
                    <td class="r muted">{{ ago(j.ts) }}</td>
                  </tr>
                </tbody>
              </table></div>
              <div v-if="!(m?.recent_jobs || []).length" class="empty-state">No automation runs yet.</div>
            </div>
            <div class="card">
              <div class="ch"><h2 class="ct">Live sessions</h2><router-link class="lk" to="/sessions" style="font-size:13px">All sessions</router-link></div>
              <div class="tw"><table class="t">
                <thead><tr><th>User</th><th>Host</th><th>Since</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
                <tbody>
                  <tr v-for="s in (m?.active_sessions || [])" :key="s.host + s.since">
                    <td><div class="row" style="gap:8px"><span class="av">{{ ini(s.user) }}</span>{{ s.user || '—' }}</div></td>
                    <td>{{ s.host }}</td>
                    <td class="num muted">{{ whenLabel(s.since) }}</td>
                    <td class="r"><router-link class="btn sm" to="/sessions">Open</router-link></td>
                  </tr>
                </tbody>
              </table></div>
              <div v-if="!(m?.active_sessions || []).length" class="empty-state">No live sessions right now.</div>
            </div>
          </section>

          <section class="card">
            <div class="ch"><h2 class="ct">Recent activity</h2><router-link v-if="auth.can('admin.audit')" class="lk" to="/settings/audit" style="font-size:13px">Open audit log</router-link></div>
            <div class="tw"><table class="t">
              <thead><tr><th>Time</th><th>Who</th><th>Action</th><th>Target</th><th>Result</th></tr></thead>
              <tbody>
                <tr v-for="a in recentActivity" :key="a.key">
                  <td class="mono muted">{{ whenLabel(a.ts) }}</td>
                  <td>{{ a.actor }}</td>
                  <td>{{ a.action }}</td>
                  <td :class="{ mono: a.mono }">{{ a.target }}</td>
                  <td><span class="b" :class="actKind(a.status)">{{ a.status }}</span></td>
                </tr>
              </tbody>
            </table></div>
            <div v-if="!recentActivity.length" class="empty-state">No sessions, runs or blocked commands recorded yet.</div>
          </section>
        </template>

        <!-- ── Non-admin tables ────────────────────────────────────────────── -->
        <section v-else class="grid2">
          <div class="card">
            <div class="ch"><h2 class="ct">Your recent runs</h2><router-link class="lk" to="/automation?tab=runs" style="font-size:13px">All your runs</router-link></div>
            <div class="tw"><table class="t">
              <thead><tr><th>Status</th><th>Playbook</th><th class="r">When</th></tr></thead>
              <tbody>
                <tr v-for="r in myRuns.slice(0, 8)" :key="r.id">
                  <td><span class="b" :class="runKind(r.status)">{{ runLabel(r.status) }}</span></td>
                  <td><router-link class="nm" :to="`/jobs/${r.id}`">{{ shortName(r.job_template_name) }}</router-link><span class="s2">{{ (r.target_host_ids || []).length }} host{{ (r.target_host_ids || []).length === 1 ? '' : 's' }}</span></td>
                  <td class="r muted">{{ ago(r.started_at) }}</td>
                </tr>
              </tbody>
            </table></div>
            <div v-if="!myRuns.length" class="empty-state">You have not started any runs yet.</div>
          </div>
          <div class="card">
            <div class="ch"><h2 class="ct">Your access</h2><router-link class="lk" to="/hosts" style="font-size:13px">Open hosts</router-link></div>
            <div class="tw"><table class="t">
              <thead><tr><th>Rule</th><th>Targets</th><th class="r">Ends</th></tr></thead>
              <tbody>
                <tr v-for="a in myAccess" :key="a.id">
                  <td><span class="nm">{{ a.name }}</span><span class="s2">{{ (a.actions || []).join(', ') }}</span></td>
                  <td>{{ targetLabel(a) }}</td>
                  <td class="r">
                    <span v-if="a.status === 'pending_approval'" class="b pend">Awaiting approval</span>
                    <span v-else-if="!a.date_expired" class="muted">No end date</span>
                    <span v-else :style="{ color: endsSoon(a) ? 'var(--warn)' : 'var(--muted)' }">{{ endsLabel(a) }}</span>
                  </td>
                </tr>
              </tbody>
            </table></div>
            <div v-if="!myAccess.length" class="empty-state">No access rules are assigned to you directly. Access granted through a group is not listed here.</div>
          </div>
        </section>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import api, { getToken } from '@/api/client'
import { pendingRuns, pendingAuthz, refreshApprovals } from '@/composables/useApprovals'

const auth = useAuthStore()
const notif = useNotificationsStore()
const isAdmin = computed(() => auth.isAdmin)
const m = ref<any>(null)
const runs = ref<any[]>([])
const sessions = ref<any[]>([])
const authz = ref<any[]>([])
const userMap = ref<Record<string, string>>({})
const hosts = ref<any[]>([])

async function load() {
  const hdr = { headers: { Authorization: `Bearer ${getToken() || ''}` } }
  const get = async (u: string) => { try { const r = await fetch(u, hdr); return r.ok ? await r.json() : null } catch { return null } }
  const [metrics, users] = await Promise.all([get('/api/v1/metrics/dashboard'), isAdmin.value ? get('/api/v1/users') : Promise.resolve(null)])
  if (metrics) m.value = metrics
  if (users) userMap.value = Object.fromEntries(users.map((u: any) => [u.id, u.username || u.name || u.id]))
  const safe = async (p: Promise<any>) => { try { return (await p).data } catch { return null } }
  const [r, s, a, h] = await Promise.all([
    safe(api.get('/job-runs')), safe(api.get('/ssh/sessions')), safe(api.get('/authorizations')), isAdmin.value ? Promise.resolve(null) : safe(api.get('/hosts')),
  ])
  runs.value = Array.isArray(r) ? r : []
  sessions.value = Array.isArray(s) ? s : []
  authz.value = Array.isArray(a) ? a : []
  hosts.value = Array.isArray(h) ? h : []
  refreshApprovals(isAdmin.value)
  notif.load().catch(() => {})
}
onMounted(load)

// ── Header ────────────────────────────────────────────────────────────────
const firstName = computed(() => (auth.user?.display_name || auth.user?.username || '').split(/\s+/)[0] || 'there')
const greeting = computed(() => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening' })
const subline = computed(() => {
  const t = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
  return isAdmin.value
    ? `What needs you${m.value?.hosts?.total != null ? ` across ${m.value.hosts.total} hosts` : ''}, as of ${t}.`
    : `Your runs, requests and access, as of ${t}.`
})

// ── Helpers ───────────────────────────────────────────────────────────────
const me = computed(() => auth.user?.id || '')
const myRuns = computed(() => runs.value.filter((r) => r.triggered_by_user_id === me.value))
const mySessions = computed(() => sessions.value.filter((s) => s.user_id === me.value))
const DAY = 86400000
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
function ini(n?: string) { return (n || '?').slice(0, 2).toUpperCase() }
function shortName(n?: string) { return n && n.length > 28 ? n.slice(0, 26) + '…' : (n || '—') }
function ago(iso?: string | null) {
  if (!iso) return '—'
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${Math.max(secs, 0)}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`
  return `${Math.floor(secs / 86400)} d ago`
}
function whenLabel(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString(undefined, { hour12: false })
    : d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function runKind(s: string) {
  if (s === 'success') return 'ok'
  if (s === 'failed' || s === 'error') return 'bad'
  if (s === 'running' || s === 'pending') return 'info'
  if (s === 'pending_approval') return 'pend'
  return 'off'
}
function runLabel(s: string) {
  return ({ success: 'Succeeded', failed: 'Failed', error: 'Error', running: 'Running', pending: 'Queued', pending_approval: 'Awaiting approval', rejected: 'Rejected', cancelled: 'Cancelled' } as Record<string, string>)[s] || s
}
function actKind(s: string) {
  const v = (s || '').toLowerCase()
  if (['active', 'success', 'ok', 'succeeded'].includes(v)) return 'ok'
  if (['failed', 'error', 'denied'].includes(v)) return 'bad'
  if (['running', 'pending'].includes(v)) return 'info'
  return 'off'
}
function jobActor(t: string): string {
  if (!t) return 'system'
  if (t.startsWith('schedule:')) return 'schedule'
  if (t.startsWith('zabbix')) return 'Zabbix problem'
  if (!t.startsWith('user:')) return 'system'
  return userMap.value[t.slice(5)] || 'deleted user'
}

// ── Needs attention ───────────────────────────────────────────────────────
interface Card { t: string; s: string; to: string; icon: string; kind: string }
const ICON = {
  check: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM8.5 12l2.5 2.5 4.5-5',
  x: 'M18 6L6 18M6 6l12 12',
  key: 'M8 11a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM10.8 12.2L20 3M16 7l3 3',
  hosts: 'M4.5 4h15v7h-15zM4.5 13h15v7h-15z',
  play: 'M8 5l11 7-11 7z',
  clock: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 7v5l3 2',
}
const failedRecent = computed(() => {
  const cut = Date.now() - 7 * DAY
  return (isAdmin.value ? runs.value : myRuns.value).filter((r) => (r.status === 'failed' || r.status === 'error') && r.started_at && new Date(r.started_at).getTime() >= cut)
})
const unreachable = computed(() => {
  if (isAdmin.value) return Math.max(0, (m.value?.hosts?.total ?? 0) - (m.value?.hosts?.reachable ?? 0))
  return hosts.value.filter((h) => h.is_reachable === false).length
})
const myPending = computed(() => myRuns.value.filter((r) => r.status === 'pending_approval').length + authz.value.filter((a) => a.status === 'pending_approval' && a.requested_by === me.value).length)
const myRunning = computed(() => myRuns.value.filter((r) => r.status === 'running'))
const endingSoon = computed(() => myAccess.value.filter(endsSoon))
const cards = computed<Card[]>(() => {
  const out: Card[] = []
  if (isAdmin.value) {
    const n = pendingRuns.value + pendingAuthz.value
    if (n) out.push({ t: `${n} waiting for approval`, s: `${pendingRuns.value} job run${pendingRuns.value === 1 ? '' : 's'} · ${pendingAuthz.value} access rule${pendingAuthz.value === 1 ? '' : 's'}`, to: '/approvals', icon: ICON.check, kind: 'pend' })
  } else {
    if (myRunning.value.length) out.push({ t: `${myRunning.value.length} run${myRunning.value.length === 1 ? '' : 's'} in progress`, s: `${shortName(myRunning.value[0].job_template_name)} · started ${ago(myRunning.value[0].started_at)}`, to: `/jobs/${myRunning.value[0].id}`, icon: ICON.play, kind: 'info' })
    if (myPending.value) out.push({ t: `${myPending.value} request${myPending.value === 1 ? '' : 's'} awaiting approval`, s: 'Waiting on an admin', to: '/approvals', icon: ICON.check, kind: 'pend' })
    if (endingSoon.value.length) out.push({ t: `Access to ${endingSoon.value[0].name} ends soon`, s: endsLabel(endingSoon.value[0]), to: '/hosts', icon: ICON.clock, kind: 'warn' })
  }
  if (failedRecent.value.length) {
    const l = failedRecent.value[0]
    out.push({ t: `${failedRecent.value.length} run${failedRecent.value.length === 1 ? '' : 's'} failed`, s: `Last one: ${shortName(l.job_template_name)}, ${whenLabel(l.started_at)}`, to: `/jobs/${l.id}`, icon: ICON.x, kind: 'bad' })
  }
  const due = m.value?.credentials?.rotation_due ?? 0
  if (isAdmin.value && due) out.push({ t: `${due} credential${due === 1 ? '' : 's'} due`, s: 'Password rotation is overdue', to: '/admin/credentials', icon: ICON.key, kind: 'warn' })
  if (unreachable.value) out.push({ t: `${unreachable.value} host${unreachable.value === 1 ? '' : 's'} unreachable`, s: isAdmin.value ? 'Failed the last reachability check' : 'Among the hosts you can reach', to: '/hosts', icon: ICON.hosts, kind: 'warn' })
  if (!out.length) out.push({ t: 'All clear', s: 'Nothing needs your attention right now.', to: '/', icon: ICON.check, kind: 'ok' })
  return out
})

// ── Stat tiles ────────────────────────────────────────────────────────────
function sparkPath(values: number[]) {
  const w = 100, h = 30, pad = 2
  const max = Math.max(1, ...values)
  const stepX = (w - pad * 2) / Math.max(1, values.length - 1)
  const pts = values.map((v, i) => [pad + i * stepX, h - pad - (v / max) * (h - pad * 2)])
  const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  return { line, area: `${line} L${pts[pts.length - 1][0].toFixed(1)},${h} L${pts[0][0].toFixed(1)},${h} Z` }
}
const dailySeries = (items: any[], ts: (x: any) => string | null | undefined, days = 7) => {
  const today = startOfDay(new Date())
  const out = new Array(days).fill(0)
  for (const it of items) {
    const t = ts(it); if (!t) continue
    const idx = days - 1 - Math.floor((today - startOfDay(new Date(t))) / DAY)
    if (idx >= 0 && idx < days) out[idx]++
  }
  return out
}
const sameDay = (iso?: string | null) => !!iso && new Date(iso).toDateString() === new Date().toDateString()
const stats = computed(() => {
  if (isAdmin.value) {
    const total = m.value?.hosts?.total ?? 0, ok = m.value?.hosts?.reachable ?? 0
    const act = m.value?.activity_7d || []
    return [
      { l: 'Active sessions', v: m.value?.sessions?.active ?? '—', d: `${m.value?.sessions?.last_24h ?? 0} opened in 24h`, to: '/sessions', spark: act.length ? sparkPath(act.map((x: any) => x.sessions)) : null },
      { l: 'Managed hosts', v: m.value?.hosts?.total ?? '—', d: `${ok} reachable`, to: '/hosts', bar: total ? { ok, bad: Math.max(total - ok, 0), okT: `${ok} reachable`, badT: `${total - ok} unreachable or failed`, label: `${ok} reachable, ${total - ok} unreachable or failed` } : null },
      { l: 'Job runs today', v: m.value?.jobs?.today ?? '—', d: `${m.value?.jobs?.ok_week ?? 0} succeeded · ${m.value?.jobs?.failed_week ?? 0} failed (7d)`, to: '/automation?tab=runs', spark: act.length ? sparkPath(act.map((x: any) => x.jobs)) : null },
      { l: 'Credentials due', v: m.value?.credentials?.rotation_due ?? 0, d: `${m.value?.credentials?.weak ?? 0} weak credentials`, to: '/admin/credentials', spark: null },
    ]
  }
  const reach = hosts.value.filter((h) => h.is_reachable !== false).length
  const total = hosts.value.length
  const mr = myRuns.value
  return [
    { l: 'Your sessions', v: mySessions.value.filter((s) => s.status === 'active').length, d: `${mySessions.value.filter((s) => new Date(s.started_at).getTime() >= Date.now() - 7 * DAY).length} opened this week`, to: '/sessions', spark: sparkPath(dailySeries(mySessions.value, (s) => s.started_at)) },
    { l: 'Hosts you can reach', v: total, d: `${reach} reachable`, to: '/hosts', bar: total ? { ok: reach, bad: total - reach, okT: `${reach} reachable`, badT: `${total - reach} unreachable`, label: `${reach} reachable, ${total - reach} unreachable` } : null },
    { l: 'Your runs today', v: mr.filter((r) => sameDay(r.started_at)).length, d: `${mr.filter((r) => r.status === 'running').length} running · ${mr.filter((r) => r.status === 'success' && sameDay(r.started_at)).length} succeeded`, to: '/automation?tab=runs', spark: sparkPath(dailySeries(mr, (r) => r.started_at)) },
    { l: 'Runs failed (7d)', v: failedRecent.value.length, d: failedRecent.value.length ? 'Open one to see why' : 'None', to: '/automation?tab=runs', spark: null },
  ]
})

// ── Activity chart ────────────────────────────────────────────────────────
const RANGES = [{ id: '7d', label: '7d', title: 'last 7 days' }, { id: '30d', label: '30d', title: 'last 30 days' }, { id: '90d', label: '3m', title: 'last 3 months' }] as const
const range = ref<'7d' | '30d' | '90d'>('7d')
const rangeTitle = computed(() => RANGES.find((r) => r.id === range.value)!.title)
const NICE = [5, 10, 15, 20, 25, 50, 100, 150, 200, 250, 500, 1000, 2000, 5000]
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const fmt = (t: number) => { const d = new Date(t); return `${MON[d.getMonth()]} ${d.getDate()}` }

// Buckets are aggregated client-side from the real lists (job runs, SSH sessions). The
// 7d view for admins uses the metrics-service daily series as-is. 3m = 13 weekly buckets.
const chart = computed(() => {
  const today = startOfDay(new Date())
  const n = range.value === '7d' ? 7 : range.value === '30d' ? 30 : 13
  const span = range.value === '90d' ? 7 * DAY : DAY
  const jobs = new Array(n).fill(0), sess = new Array(n).fill(0)
  const bucket = (iso?: string | null) => {
    if (!iso) return -1
    const age = Math.floor((today + DAY - 1 - new Date(iso).getTime()) / span)   // 0 = newest bucket
    const idx = n - 1 - age
    return age >= 0 && idx >= 0 && idx < n ? idx : -1
  }
  const runSrc = isAdmin.value ? runs.value : myRuns.value
  const sesSrc = isAdmin.value ? sessions.value : mySessions.value
  const useMetrics = isAdmin.value && range.value === '7d' && (m.value?.activity_7d || []).length === 7
  if (useMetrics) {
    m.value.activity_7d.forEach((d: any, i: number) => { jobs[i] = d.jobs || 0; sess[i] = d.sessions || 0 })
  } else {
    for (const r of runSrc) { const i = bucket(r.started_at); if (i >= 0) jobs[i]++ }
    for (const s of sesSrc) { const i = bucket(s.started_at); if (i >= 0) sess[i]++ }
  }
  const max = Math.max(1, ...jobs, ...sess)
  const step = NICE.find((x) => x * 3 >= max) ?? Math.ceil(max / 3)
  const top = step * 3
  const days = jobs.map((j, i) => {
    const end = today - (n - 1 - i) * span
    const startT = range.value === '90d' ? end - 6 * DAY : end
    const when = range.value === '90d' ? `Week of ${fmt(startT)}` : fmt(end)
    const tick = range.value === '7d' ? DOW[new Date(end).getDay()] : range.value === '30d' ? ((n - 1 - i) % 5 === 0 ? fmt(end) : '') : ((n - 1 - i) % 2 === 0 ? fmt(startT) : '')
    return { when, tick, label: `${when}: ${j} runs, ${sess[i]} sessions`, jh: Math.round((j / top) * 160), sh: Math.round((sess[i] / top) * 160) }
  })
  const oldest = runSrc.length ? new Date(runSrc[runSrc.length - 1].started_at || 0).getTime() : 0
  const rangeStart = today - (n * span / DAY - 1) * DAY
  const capped = !useMetrics && runs.value.length >= 200 && oldest > rangeStart
  return {
    n, days, ticks: [top, step * 2, step, 0], gap: n > 20 ? 3 : n > 10 ? 8 : 12,
    note: capped ? 'Job runs are counted from the latest 200 runs the API returns, so older weeks may be understated.' : '',
  }
})

// ── Admin: recent activity feed ───────────────────────────────────────────
interface ActivityRow { key: string; ts: string; actor: string; action: string; target: string; status: string; mono?: boolean }
const recentActivity = computed<ActivityRow[]>(() => {
  const rows: ActivityRow[] = []
  for (const s of (m.value?.active_sessions || [])) rows.push({ key: `s-${s.host}-${s.since}`, ts: s.since, actor: s.user || '—', action: 'SSH session opened', target: s.host, status: 'active' })
  for (const j of (m.value?.recent_jobs || [])) rows.push({ key: `j-${j.id}`, ts: j.ts, actor: jobActor(j.triggered_by), action: 'Run started', target: shortName(j.playbook), status: j.status })
  for (const b of (m.value?.acl_blocks || [])) rows.push({ key: `b-${b.ts}-${b.command}`, ts: b.ts, actor: b.user || 'unknown', action: 'Command blocked', target: b.command, status: 'denied', mono: true })
  return rows.filter((r) => r.ts).sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime()).slice(0, 12)
})

// ── Non-admin: your access ────────────────────────────────────────────────
const myAccess = computed(() => authz.value.filter((a) => (a.user_ids || []).includes(me.value) || a.user_id === me.value))
function targetLabel(a: any) {
  const h = (a.host_ids || []).length + (a.host_id ? 1 : 0), g = (a.host_group_ids || []).length + (a.host_group_id ? 1 : 0)
  return [h ? `${h} host${h === 1 ? '' : 's'}` : '', g ? `${g} group${g === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ') || '—'
}
function endsSoon(a: any) { if (!a.date_expired) return false; const d = new Date(a.date_expired).getTime() - Date.now(); return d > 0 && d < DAY }
function endsLabel(a: any) {
  const d = new Date(a.date_expired).getTime() - Date.now()
  if (d <= 0) return 'Ended'
  if (d < DAY) return `In ${Math.max(1, Math.round(d / 3600000))} h`
  return new Date(a.date_expired).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
</script>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(var(--min, 220px), 1fr)); gap: 12px; }
.att { text-decoration: none; color: var(--text); padding: 14px 16px; display: flex; gap: 12px; align-items: center; }
.att-s { display: block; font-size: 12.5px; color: var(--muted); }
.statlink { text-decoration: none; color: var(--text); }
.statd { font-size: 12.5px; color: var(--muted); }
.hbar { display: flex; gap: 2px; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 14px; }
.spark { width: 100%; height: 30px; display: block; margin-top: 10px; }
.spark-area { fill: color-mix(in srgb, var(--primary) 14%, transparent); stroke: none; }
.spark-line { fill: none; stroke: var(--primary); stroke-width: 1.8; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.sw { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.yaxis { display: flex; flex-direction: column; justify-content: space-between; height: 160px; font-size: 11.5px; color: var(--muted); text-align: right; width: 30px; }
.plot { height: 160px; display: grid; align-items: end; background: linear-gradient(var(--border) 1px, transparent 1px) 0 0 / 100% 53.33px; border-bottom: 1px solid var(--border2); }
.pcol { display: flex; gap: 4px; align-items: flex-end; justify-content: center; height: 100%; }
.pbar { width: 40%; max-width: 22px; border-radius: 3px 3px 0 0; min-height: 0; }
.xaxis { display: grid; font-size: 12px; color: var(--muted); text-align: center; }
.xaxis span { white-space: nowrap; overflow: visible; }
</style>
