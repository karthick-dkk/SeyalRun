<template>
  <AppShell>
    <div class="page">
      <div class="page-header">
        <div>
          <div class="page-title">Dashboard</div>
          <div class="page-subtitle">Welcome back, {{ auth.user?.username }}</div>
        </div>
        <button class="btn btn-icon" title="Refresh" @click="load"><svg style="width:14px;height:14px;display:block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg></button>
      </div>

      <!-- ── Stat row ─────────────────────────────────────────────────────── -->
      <div class="stat-row">
        <div class="stat">
          <div class="stat-label">Active Sessions</div>
          <div class="stat-value stat-accent">{{ m?.sessions?.active ?? '—' }}</div>
          <div class="stat-sub">{{ m?.sessions?.last_24h ?? 0 }} opened in 24h</div>
          <svg v-if="sessionsSpark" class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
            <path :d="sessionsSpark.area" class="spark-area" />
            <path :d="sessionsSpark.line" class="spark-line" />
            <circle :cx="sessionsSpark.lastX" :cy="sessionsSpark.lastY" r="2.3" class="spark-dot" />
          </svg>
        </div>

        <div class="stat">
          <div class="stat-label">Managed Hosts</div>
          <div class="stat-value">{{ m?.hosts?.total ?? '—' }}</div>
          <div class="stat-sub">{{ m?.hosts?.reachable ?? 0 }} reachable</div>
        </div>

        <div class="stat">
          <div class="stat-label">Jobs Today</div>
          <div class="stat-value">{{ m?.jobs?.today ?? '—' }}</div>
          <div class="stat-sub">{{ m?.jobs?.ok_week ?? 0 }} ok · {{ m?.jobs?.failed_week ?? 0 }} failed (7d)</div>
          <svg v-if="jobsSpark" class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">
            <path :d="jobsSpark.area" class="spark-area" />
            <path :d="jobsSpark.line" class="spark-line" />
            <circle :cx="jobsSpark.lastX" :cy="jobsSpark.lastY" r="2.3" class="spark-dot" />
          </svg>
        </div>

        <div class="stat" :class="{ 'stat-warn': fourthValue > 0 }">
          <div class="stat-label">{{ auth.isAdmin ? 'Rotation Due' : 'Failed Sessions' }}</div>
          <div class="stat-value">{{ fourthValue }}</div>
          <div class="stat-sub">{{ auth.isAdmin ? `${m?.credentials?.weak ?? 0} weak credentials` : 'in the last 24h' }}</div>
        </div>
      </div>

      <!-- ── 7-Day Activity ───────────────────────────────────────────────── -->
      <div class="card" style="margin-bottom:16px">
        <div class="card-header">7-Day Activity</div>
        <div class="chart">
          <div v-for="d in (m?.activity_7d || [])" :key="d.date" class="chart-col">
            <div class="chart-bars">
              <div class="chart-bar chart-bar--jobs" :style="{ height: barH(d.jobs) }" :title="`${d.jobs} jobs`"></div>
              <div class="chart-bar chart-bar--sessions" :style="{ height: barH(d.sessions) }" :title="`${d.sessions} sessions`"></div>
            </div>
            <div class="chart-label">{{ shortDate(d.date) }}</div>
          </div>
          <div v-if="!(m?.activity_7d || []).length" class="muted" style="padding:24px">No activity data.</div>
        </div>
        <div class="chart-legend">
          <span><i class="dot dot--jobs"></i> Jobs</span>
          <span><i class="dot dot--sessions"></i> Sessions</span>
        </div>
      </div>

      <!-- ── Recent Jobs + Top Playbooks ──────────────────────────────────── -->
      <div class="grid-2" style="margin-bottom:16px">
        <div class="card">
          <div class="card-header">Recent Jobs <router-link to="/jobs" class="hdr-link">All Jobs →</router-link></div>
          <table class="table" style="font-size:13px">
            <thead><tr><th>Status</th><th>Playbook</th><th>Triggered</th><th>When</th></tr></thead>
            <tbody>
              <tr v-for="j in (m?.recent_jobs || [])" :key="j.id" style="cursor:pointer" @click="$router.push(`/jobs/${j.id}`)">
                <td><span :class="statusBadge(j.status)">{{ j.status }}</span></td>
                <td>{{ shortName(j.playbook) }}</td>
                <td style="color:var(--text2)">{{ trigLabel(j.triggered_by) }}</td>
                <td style="color:var(--text2)">{{ ago(j.ts) }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="!(m?.recent_jobs || []).length" class="muted" style="padding:20px">No jobs yet.</div>
        </div>

        <div class="card">
          <div class="card-header">Top Playbooks</div>
          <table class="table" style="font-size:13px">
            <thead><tr><th>Playbook</th><th class="r">Runs</th></tr></thead>
            <tbody>
              <tr v-for="p in (m?.top_playbooks || [])" :key="p.name">
                <td>{{ shortName(p.name) }}</td><td class="r"><span class="badge badge-blue">{{ p.runs }}</span></td>
              </tr>
            </tbody>
          </table>
          <div v-if="!(m?.top_playbooks || []).length" class="muted" style="padding:20px">No runs yet.</div>
        </div>
      </div>

      <!-- ── Recent activity ──────────────────────────────────────────────── -->
      <div class="card">
        <div class="card-header">
          <div>
            <div>Recent activity</div>
            <div class="card-sub">Last actions across sessions, jobs{{ auth.isAdmin ? ' and access grants' : '' }}.</div>
          </div>
          <router-link to="/sessions" class="hdr-link">All Sessions →</router-link>
        </div>
        <table class="table activity-table">
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Status</th></tr></thead>
          <tbody>
            <tr v-for="a in recentActivity" :key="a.key">
              <td class="mono">{{ whenLabel(a.ts) }}</td>
              <td>{{ a.actor }}</td>
              <td>{{ a.action }}</td>
              <td :class="{ mono: a.mono }">{{ a.target }}</td>
              <td><span :class="pillClass(a.status)">{{ a.status }}</span></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!recentActivity.length" class="muted" style="padding:24px">No activity yet.</div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { useAuthStore } from '@/stores/auth'
import { getToken } from '@/api/client'

const auth = useAuthStore()
const m = ref<any>(null)
// Best-effort user-id -> username lookup for "Trigger -> playbook" rows in Recent
// activity, which otherwise show the raw triggered_by UUID. /users degrades
// gracefully (stays empty) for callers without list access.
const userMap = ref<Record<string, string>>({})

async function load() {
  try {
    const r = await fetch('/api/v1/metrics/dashboard', { headers: { Authorization: `Bearer ${getToken() || ''}` } })
    if (r.ok) m.value = await r.json()
  } catch { /* metrics-service optional */ }
  try {
    const r = await fetch('/api/v1/users', { headers: { Authorization: `Bearer ${getToken() || ''}` } })
    if (r.ok) {
      const users = await r.json()
      userMap.value = Object.fromEntries(users.map((u: any) => [u.id, u.username || u.name || u.id]))
    }
  } catch { /* non-admin callers can't list users — falls back to raw id */ }
}

// SeyalRun's RBAC model has no "pending access request / approval" workflow — grants
// are assigned directly by an admin (za_authorization rows), not requested and queued.
// Rather than fabricate a number for a feature that doesn't exist, the 4th card shows a
// real, already-computed risk signal: credential rotations overdue for admins (the same
// data the old risk row showed), or a safe aggregate (failed sessions) for everyone else.
const fourthValue = computed(() =>
  auth.isAdmin ? (m.value?.credentials?.rotation_due ?? 0) : (m.value?.sessions?.failed_24h ?? 0),
)

// ── Sparklines ───────────────────────────────────────────────────────────
// Only built for metrics with a real daily series (activity_7d). Host/credential
// counts are point-in-time totals with no stored history, so those two stat cards
// intentionally render without a chart rather than fake one.
function sparklinePath(values: number[]) {
  const w = 100, h = 30, pad = 2
  const max = Math.max(1, ...values)
  const stepX = (w - pad * 2) / Math.max(1, values.length - 1)
  const pts = values.map((v, i) => [pad + i * stepX, h - pad - (v / max) * (h - pad * 2)])
  const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [fx] = pts[0]
  const [lx] = pts[pts.length - 1]
  return { line, area: `${line} L${lx.toFixed(1)},${h} L${fx.toFixed(1)},${h} Z`, lastX: lx, lastY: pts[pts.length - 1][1] }
}
const sessionsSpark = computed(() => {
  const a = m.value?.activity_7d
  return a?.length ? sparklinePath(a.map((d: any) => d.sessions)) : null
})
const jobsSpark = computed(() => {
  const a = m.value?.activity_7d
  return a?.length ? sparklinePath(a.map((d: any) => d.jobs)) : null
})

// ── Unified "recent activity" feed ──────────────────────────────────────
// Merges the three real event sources metrics-service already exposes. ACL blocks
// carry attempted command text and are only merged in for admins — the same
// visibility split the old dashboard's separate "Recent ACL Blocks" card used.
interface ActivityRow { key: string; ts: string; actor: string; action: string; target: string; status: string; mono?: boolean }

const recentActivity = computed<ActivityRow[]>(() => {
  const rows: ActivityRow[] = []
  for (const s of (m.value?.active_sessions || [])) {
    rows.push({ key: `s-${s.host}-${s.since}`, ts: s.since, actor: s.user || '—', action: 'SSH session opened', target: s.host, status: 'active' })
  }
  for (const j of (m.value?.recent_jobs || [])) {
    rows.push({ key: `j-${j.id}`, ts: j.ts, actor: jobActor(j.triggered_by), action: 'Trigger → playbook', target: shortName(j.playbook), status: j.status })
  }
  if (auth.isAdmin) {
    for (const b of (m.value?.acl_blocks || [])) {
      rows.push({ key: `b-${b.ts}-${b.command}`, ts: b.ts, actor: b.user || 'unknown', action: 'Command blocked', target: b.command, status: 'denied', mono: true })
    }
  }
  return rows
    .filter((r) => r.ts)
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    .slice(0, 12)
})

function jobActor(triggeredBy: string): string {
  if (!triggeredBy) return 'system'
  if (!triggeredBy.startsWith('user:')) return 'system'
  const userId = triggeredBy.slice(5)
  return userMap.value[userId] || userId
}
function shortName(n: string) { return n && n.length > 26 ? n.slice(0, 24) + '…' : (n || '—') }

// ── 7-Day Activity chart + Recent Jobs / Top Playbooks ────────────────────
const maxActivity = computed(() => {
  const a = m.value?.activity_7d || []
  return Math.max(1, ...a.map((d: any) => Math.max(d.jobs, d.sessions)))
})
function barH(v: number) { return `${Math.round((v / maxActivity.value) * 100)}%` }
function shortDate(iso: string) {
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString(undefined, { month: 'short', day: '2-digit' })
}
function statusBadge(s: string) {
  if (s === 'success') return 'badge badge-green'
  if (s === 'failed' || s === 'error') return 'badge badge-red'
  if (s === 'running' || s === 'pending') return 'badge badge-blue'
  return 'badge badge-gray'
}
function trigLabel(t: string) {
  if (!t) return '—'
  if (t.startsWith('user:')) return 'Manual'
  if (t.startsWith('schedule:')) return 'Schedule'
  if (t.startsWith('zabbix')) return 'Zabbix'
  return t.split(':')[0]
}
function ago(iso?: string | null) {
  if (!iso) return '—'
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}
function whenLabel(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay
    ? d.toLocaleTimeString(undefined, { hour12: false })
    : d.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function pillClass(status: string): string {
  const s = (status || '').toLowerCase()
  if (['active', 'success', 'ok'].includes(s)) return 'pill pill-ok'
  if (['failed', 'error', 'denied'].includes(s)) return 'pill pill-bad'
  if (['running', 'pending'].includes(s)) return 'pill pill-info'
  return 'pill pill-muted'
}

onMounted(load)
</script>

<style scoped>
.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 16px; }
.stat { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; display: flex; flex-direction: column; }
.stat-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text2); }
.stat-value { font-size: 32px; font-weight: 700; color: var(--text); line-height: 1.15; margin-top: 8px; font-variant-numeric: tabular-nums; }
.stat-accent { color: #58a6ff; }
.stat-sub { font-size: 12px; color: var(--text2); margin-top: 4px; }
.stat-warn .stat-value { color: #e3b341; }

.spark { width: 100%; height: 42px; display: block; margin-top: 12px; }
.spark-area { fill: rgba(59, 130, 246, 0.16); stroke: none; }
.spark-line { fill: none; stroke: #3b82f6; stroke-width: 1.8; stroke-linejoin: round; stroke-linecap: round; }
.spark-dot { fill: #3b82f6; }

.card-header { align-items: flex-start; }
.card-sub { font-size: 12px; font-weight: 400; color: var(--text2); margin-top: 2px; }
.hdr-link { font-size: 12px; font-weight: 500; color: var(--accent2); text-decoration: none; white-space: nowrap; }
.activity-table td { font-size: 13px; }
.mono { font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace; font-size: 12.5px; color: var(--text2); }
.muted { text-align: center; color: var(--text2); font-size: 13px; }

.chart { display: flex; align-items: flex-end; gap: 4px; height: 150px; padding: 16px 16px 0; }
.chart-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }
.chart-bars { display: flex; align-items: flex-end; gap: 3px; height: 110px; }
.chart-bar { width: 12px; border-radius: 3px 3px 0 0; min-height: 2px; transition: height 0.3s; }
.chart-bar--jobs { background: #58a6ff; }
.chart-bar--sessions { background: #3fb950; }
.chart-label { font-size: 11px; color: var(--text2); margin-top: 8px; }
.chart-legend { display: flex; gap: 18px; padding: 8px 16px 14px; font-size: 12px; color: var(--text2); }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; }
.dot--jobs { background: #58a6ff; }
.dot--sessions { background: #3fb950; }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) { .grid-2 { grid-template-columns: 1fr; } }
.table .r { text-align: right; }

.pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px; border-radius: 999px; font-size: 11.5px; font-weight: 600; border: 1px solid transparent; text-transform: capitalize; }
.pill::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.pill-ok { color: #3fb950; background: rgba(63, 185, 80, 0.12); border-color: rgba(63, 185, 80, 0.3); }
.pill-bad { color: #f85149; background: rgba(248, 81, 73, 0.12); border-color: rgba(248, 81, 73, 0.3); }
.pill-info { color: #58a6ff; background: rgba(88, 166, 255, 0.12); border-color: rgba(88, 166, 255, 0.3); }
.pill-muted { color: var(--text2); background: rgba(107, 118, 144, 0.12); border-color: rgba(107, 118, 144, 0.25); }
</style>
