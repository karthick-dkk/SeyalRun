<template>
  <AppShell>
    <div class="dashboard">

      <!-- Header -->
      <div class="dash-header">
        <div>
          <h2 class="dash-title">Dashboard</h2>
          <p class="dash-sub text-muted">System overview · {{ timeNow }}</p>
        </div>
        <button class="btn-refresh" @click="load" :disabled="loading" title="Refresh">↺ Refresh</button>
      </div>

      <!-- Stat cards -->
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-icon" style="color: var(--accent)">⊛</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.total_assets }}</div>
            <div class="stat-label">Total Assets</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon" style="color: var(--success)">⊙</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.active_sessions }}</div>
            <div class="stat-label">Active Sessions</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon" style="color: var(--warning)">◫</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.jobs_today }}</div>
            <div class="stat-label">Jobs Today</div>
          </div>
        </div>
        <div class="stat-card" :class="{ 'stat-alert': stats.failed_logins_24h > 0 }">
          <div class="stat-icon" style="color: var(--error)">✗</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.failed_logins_24h }}</div>
            <div class="stat-label">Failed Logins (24h)</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon" style="color: var(--success)">⬡</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.total_playbooks }}</div>
            <div class="stat-label">Playbooks</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon" :style="{ color: stats.success_rate >= 80 ? 'var(--success)' : 'var(--warning)' }">◈</div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.success_rate }}%</div>
            <div class="stat-label">Job Success Rate</div>
          </div>
        </div>
      </div>

      <!-- Activity chart + Recent sessions -->
      <div class="row-2">

        <!-- Activity chart -->
        <div class="card chart-card">
          <div class="card-header">
            <h3 class="card-title">User Activity — Last 7 Days</h3>
            <div class="chart-legend">
              <span class="legend-dot" style="background:var(--accent)" /> SSH Sessions
              <span class="legend-dot" style="background:var(--success)" /> Playbook Jobs
            </div>
          </div>
          <div class="chart-area">
            <div class="chart-bars">
              <div
                v-for="(label, i) in chart.labels"
                :key="i"
                class="chart-col"
              >
                <div class="bar-group">
                  <div
                    class="bar bar-sessions"
                    :style="{ height: barHeight(chart.sessions[i], maxChartVal) + 'px' }"
                    :title="`${chart.sessions[i]} SSH sessions`"
                  />
                  <div
                    class="bar bar-jobs"
                    :style="{ height: barHeight(chart.jobs[i], maxChartVal) + 'px' }"
                    :title="`${chart.jobs[i]} jobs`"
                  />
                </div>
                <div class="bar-label">{{ label }}</div>
              </div>
            </div>
            <div class="chart-empty" v-if="maxChartVal === 0">No activity in the last 7 days</div>
          </div>
        </div>

        <!-- Recent logins -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Recent SSH Logins</h3>
            <router-link to="/sessions" class="card-link">All →</router-link>
          </div>
          <div v-if="recentSessions.length === 0" class="card-empty">No sessions yet</div>
          <div v-else class="session-list">
            <div v-for="s in recentSessions" :key="s.id" class="session-row">
              <span class="sess-dot" :class="`sdot-${s.status}`" />
              <div class="sess-info">
                <div class="sess-host">{{ s.ssh_username }}@{{ s.asset_address }}</div>
                <div class="sess-meta text-muted">by {{ s.user }} · {{ relTime(s.started_at) }}</div>
              </div>
              <span class="sess-duration mono" v-if="s.duration_seconds">
                {{ fmtDuration(s.duration_seconds) }}
              </span>
              <span :class="`badge badge-${statusBadge(s.status)}`">{{ s.status }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Playbook history + Failed logins -->
      <div class="row-2">

        <!-- Playbook run history -->
        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Playbook Run History</h3>
            <router-link to="/jobs" class="card-link">All →</router-link>
          </div>
          <div v-if="recentJobs.length === 0" class="card-empty">No jobs yet</div>
          <div v-else class="jobs-table">
            <div class="jobs-header">
              <span>Status</span><span>Triggered by</span><span>Duration</span><span>When</span>
            </div>
            <div v-for="j in recentJobs" :key="j.id" class="jobs-row">
              <span :class="`badge badge-${statusBadge(j.status)}`">{{ j.status }}</span>
              <span class="mono">{{ j.triggered_by }}</span>
              <span class="mono text-muted">{{ j.duration_seconds ? fmtDuration(j.duration_seconds) : '—' }}</span>
              <span class="text-muted">{{ relTime(j.created_at) }}</span>
            </div>
          </div>
        </div>

        <!-- Failed login attempts -->
        <div class="card" :class="{ 'card-warn': failedSessions.length > 0 }">
          <div class="card-header">
            <h3 class="card-title">
              <span v-if="failedSessions.length > 0" class="warn-icon">⚠</span>
              Failed Login Attempts
            </h3>
            <span class="failed-count" v-if="failedSessions.length > 0">{{ failedSessions.length }} total</span>
          </div>
          <div v-if="failedSessions.length === 0" class="card-empty card-empty-ok">
            <span style="color:var(--success)">✓</span> No failed logins recorded
          </div>
          <div v-else class="failed-list">
            <div v-for="s in failedSessions" :key="s.id" class="failed-row">
              <span class="fail-icon">✗</span>
              <div class="fail-info">
                <div class="fail-host">{{ s.ssh_username }}@{{ s.asset_address }}</div>
                <div class="fail-meta text-muted">by {{ s.user }} · {{ relTime(s.started_at) }}</div>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import { api } from '@/api/client'

interface Stats {
  total_assets: number; total_playbooks: number; total_sessions: number
  active_sessions: number; failed_logins_24h: number; total_jobs: number
  jobs_today: number; jobs_success: number; jobs_failed: number; success_rate: number
}
interface ChartData { labels: string[]; sessions: number[]; jobs: number[] }

const loading = ref(false)
const stats = ref<Stats>({
  total_assets: 0, total_playbooks: 0, total_sessions: 0, active_sessions: 0,
  failed_logins_24h: 0, total_jobs: 0, jobs_today: 0, jobs_success: 0,
  jobs_failed: 0, success_rate: 0,
})
const chart = ref<ChartData>({ labels: [], sessions: [], jobs: [] })
const recentSessions = ref<any[]>([])
const failedSessions = ref<any[]>([])
const recentJobs = ref<any[]>([])

const timeNow = new Date().toLocaleString()
const maxChartVal = computed(() =>
  Math.max(1, ...chart.value.sessions, ...chart.value.jobs)
)

function barHeight(val: number, max: number) {
  return Math.max(2, Math.round((val / max) * 120))
}

function statusBadge(s: string) {
  return { success: 'success', failed: 'error', running: 'info', pending: 'neutral',
           active: 'success', closed: 'neutral', error: 'error', cancelled: 'neutral' }[s] ?? 'neutral'
}

function relTime(iso?: string) {
  if (!iso) return '—'
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmtDuration(s: number) {
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

async function load() {
  loading.value = true
  try {
    const resp = await api.get('/dashboard')
    stats.value = resp.data.stats
    chart.value = resp.data.activity_chart
    recentSessions.value = resp.data.recent_sessions
    failedSessions.value = resp.data.failed_sessions
    recentJobs.value = resp.data.recent_jobs
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: var(--space-5); }

.dash-header { display: flex; justify-content: space-between; align-items: flex-start; }
.dash-title { font-size: 20px; font-weight: 700; }
.dash-sub { font-size: 12px; margin-top: 2px; }
.btn-refresh { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 12px; cursor: pointer; transition: all var(--transition); }
.btn-refresh:hover:not(:disabled) { color: var(--text); border-color: var(--border-muted); }
.btn-refresh:disabled { opacity: 0.4; cursor: not-allowed; }

/* Stat cards */
.stat-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: var(--space-4); }
@media (max-width: 1200px) { .stat-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 768px)  { .stat-grid { grid-template-columns: repeat(2, 1fr); } }

.stat-card {
  background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: var(--space-4); display: flex; align-items: center; gap: var(--space-3);
  transition: all var(--transition);
}
.stat-card:hover { border-color: var(--border-muted); box-shadow: var(--shadow-sm); }
.stat-alert { border-color: var(--error); background: var(--error-dim); }
.stat-icon { font-size: 22px; flex-shrink: 0; }
.stat-value { font-size: 28px; font-weight: 700; font-family: var(--font-mono); line-height: 1; }
.stat-label { font-size: 11px; color: var(--text-muted); margin-top: 3px; }

/* 2-col row */
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
@media (max-width: 900px) { .row-2 { grid-template-columns: 1fr; } }

/* Card */
.card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }
.card-warn { border-color: var(--error); }
.card-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); }
.card-title { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: var(--space-2); }
.card-link { font-size: 12px; color: var(--accent); text-decoration: none; }
.card-empty { padding: var(--space-6); text-align: center; color: var(--text-muted); font-size: 13px; }
.card-empty-ok { display: flex; align-items: center; justify-content: center; gap: var(--space-2); }
.warn-icon { color: var(--error); }
.failed-count { font-size: 11px; font-weight: 700; color: var(--error); background: var(--error-dim); padding: 2px 8px; border-radius: var(--radius-full); }

/* Chart */
.chart-card { }
.chart-legend { display: flex; align-items: center; gap: var(--space-3); font-size: 11px; color: var(--text-muted); }
.legend-dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; margin-right: 4px; }
.chart-area { padding: var(--space-4) var(--space-5) var(--space-3); position: relative; }
.chart-bars { display: flex; align-items: flex-end; gap: 0; height: 140px; }
.chart-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.bar-group { display: flex; align-items: flex-end; gap: 2px; width: 100%; justify-content: center; }
.bar { width: 10px; border-radius: 3px 3px 0 0; transition: height 400ms ease; min-height: 2px; }
.bar-sessions { background: var(--accent); }
.bar-jobs { background: var(--success); }
.bar-label { font-size: 9px; color: var(--text-subtle); font-family: var(--font-mono); margin-top: 4px; white-space: nowrap; }
.chart-empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--text-subtle); font-size: 12px; }

/* Session list */
.session-list { display: flex; flex-direction: column; }
.session-row { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3) var(--space-5); border-top: 1px solid var(--border); transition: background var(--transition); }
.session-row:hover { background: var(--bg-overlay); }
.sess-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.sdot-active { background: var(--success); box-shadow: 0 0 5px var(--success); }
.sdot-closed { background: var(--text-subtle); }
.sdot-error  { background: var(--error); }
.sess-info { flex: 1; min-width: 0; }
.sess-host { font-size: 12px; font-weight: 500; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-meta { font-size: 11px; }
.sess-duration { font-size: 11px; }

/* Jobs table */
.jobs-table { display: flex; flex-direction: column; }
.jobs-header { display: grid; grid-template-columns: 80px 1fr 80px 100px; padding: var(--space-2) var(--space-5); font-size: 10px; font-weight: 700; color: var(--text-subtle); text-transform: uppercase; letter-spacing: 0.06em; border-top: 1px solid var(--border); background: var(--bg-overlay); }
.jobs-row { display: grid; grid-template-columns: 80px 1fr 80px 100px; padding: var(--space-3) var(--space-5); font-size: 12px; align-items: center; border-top: 1px solid var(--border); transition: background var(--transition); }
.jobs-row:hover { background: var(--bg-overlay); }

/* Failed list */
.failed-list { display: flex; flex-direction: column; }
.failed-row { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3) var(--space-5); border-top: 1px solid var(--border); }
.fail-icon { color: var(--error); font-size: 13px; font-weight: 700; flex-shrink: 0; }
.fail-info { flex: 1; min-width: 0; }
.fail-host { font-size: 12px; font-weight: 500; font-family: var(--font-mono); color: var(--error); }
.fail-meta { font-size: 11px; }

.text-muted { color: var(--text-muted); }
.mono { font-family: var(--font-mono); }
</style>
