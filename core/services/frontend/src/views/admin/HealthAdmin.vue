<template>
  <div>
    <div class="card" style="margin-bottom:16px">
      <div class="card-header">
        Service Health &amp; API Status
        <button class="btn btn-sm" @click="load">Refresh</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Service</th><th>Status</th><th>HTTP</th><th>Probe</th><th>Avg / p95 (gateway)</th><th>Requests</th><th>Detail</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in health.services" :key="s.service">
            <td style="font-weight:600">{{ s.service }}</td>
            <td><span class="badge" :class="statusClass(s.status)">{{ s.status }}</span></td>
            <td style="color:var(--text2);font-size:12px">{{ s.http || '—' }}</td>
            <td class="ip-mono" style="font-size:12px">{{ s.latency_ms }} ms</td>
            <td class="ip-mono" style="font-size:12px">{{ latFor(s.service) }}</td>
            <td class="ip-mono" style="font-size:12px">{{ reqFor(s.service) }}</td>
            <td style="color:var(--text2);font-size:12px">{{ s.detail }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="loading && !health.services.length" style="padding:16px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <div class="card">
      <div class="card-header">Platform Metrics</div>
      <div class="hm-kpis">
        <div class="hm-kpi"><div class="hm-num">{{ m.hosts?.total ?? '—' }}</div><div class="hm-lbl">Hosts <span v-if="m.hosts">· {{ m.hosts.enabled }} enabled</span></div></div>
        <div class="hm-kpi"><div class="hm-num">{{ m.sessions?.active ?? '—' }}</div><div class="hm-lbl">Active sessions</div></div>
        <div class="hm-kpi"><div class="hm-num">{{ m.sessions?.last_24h ?? '—' }}</div><div class="hm-lbl">Sessions (24h)</div></div>
        <div class="hm-kpi"><div class="hm-num">{{ m.credentials?.total ?? '—' }}</div><div class="hm-lbl">Credentials <span v-if="m.credentials">· {{ m.credentials.weak }} weak</span></div></div>
        <div class="hm-kpi"><div class="hm-num">{{ (m.jobs?.last_24h?.success ?? 0) }} / {{ (m.jobs?.last_24h?.failed ?? 0) }}</div><div class="hm-lbl">Jobs 24h (ok / fail)</div></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '@/api/client'

const health = ref<any>({ services: [], latency: {} })
const m = ref<any>({})
const loading = ref(false)

function statusClass(s: string) {
  if (s === 'ok') return 'badge-green'
  if (s === 'degraded') return 'badge-orange'
  return 'badge-red'
}
function latFor(svc: string) {
  const l = health.value.latency?.[svc]
  return l ? `${Math.round(l.avg_ms)} / ${Math.round(l.p95_ms)} ms` : '—'
}
function reqFor(svc: string) {
  const l = health.value.latency?.[svc]
  return l ? `${l.count}${l.errors ? ' · ' + l.errors + ' err' : ''}` : '—'
}

async function load() {
  loading.value = true
  try {
    const [h, d] = await Promise.all([
      api.get('/admin/health').then(r => r.data).catch(() => ({ services: [], latency: {} })),
      api.get('/metrics/dashboard').then(r => r.data).catch(() => ({})),
    ])
    health.value = h
    m.value = d
  } finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.hm-kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; padding: 16px; }
.hm-kpi { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.hm-num { font-size: 24px; font-weight: 700; }
.hm-lbl { font-size: 12px; color: var(--text2); margin-top: 4px; }
</style>
