<template>
  <div class="stack">
    <section class="card hm-banner" :class="allOk ? 'hm-ok' : 'hm-bad'" role="status">
      <span class="b nd hm-ico" :class="allOk ? 'ok' : 'warn'" aria-hidden="true">{{ allOk ? '✓' : '!' }}</span>
      <div style="flex:1 1 300px">
        <b>{{ bannerText }}</b>
        <p class="sub">SeyalRun's own services, probed on request.</p>
      </div>
      <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" :disabled="loading" @click="load">↺</button>
    </section>

    <section class="card">
      <div class="ch"><h2 class="ct">Services <span class="cnt">{{ health.services.length }}</span></h2></div>
      <div class="tw">
        <table class="t">
          <thead>
            <tr><th>Service</th><th>Status</th><th>HTTP</th><th class="r">Probe</th><th class="r">Avg / p95 (gateway)</th><th class="r">Requests</th><th>Detail</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in health.services" :key="s.service">
              <td><span class="nm">{{ s.service }}</span></td>
              <td><span class="b" :class="statusClass(s.status)">{{ s.status }}</span></td>
              <td class="muted">{{ s.http || '—' }}</td>
              <td class="r num mono">{{ s.latency_ms }} ms</td>
              <td class="r num mono">{{ latFor(s.service) }}</td>
              <td class="r num mono">{{ reqFor(s.service) }}</td>
              <td class="muted">{{ s.detail }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="loading && !health.services.length" class="empty-state">Loading…</div>
      <div v-else-if="!health.services.length" class="empty-state"><b>No health data</b>The health endpoint returned no services.</div>
    </section>

    <section aria-label="Platform metrics" class="hm-kpis">
      <div class="card stat"><span class="l">Hosts</span><span class="v">{{ m.hosts?.total ?? '—' }}</span><span class="hm-d" v-if="m.hosts">{{ m.hosts.enabled }} enabled</span></div>
      <div class="card stat"><span class="l">Active sessions</span><span class="v">{{ m.sessions?.active ?? '—' }}</span></div>
      <div class="card stat"><span class="l">Sessions (24h)</span><span class="v">{{ m.sessions?.last_24h ?? '—' }}</span></div>
      <div class="card stat"><span class="l">Credentials</span><span class="v">{{ m.credentials?.total ?? '—' }}</span><span class="hm-d" v-if="m.credentials">{{ m.credentials.weak }} weak</span></div>
      <div class="card stat"><span class="l">Jobs 24h (ok / fail)</span><span class="v">{{ (m.jobs?.last_24h?.success ?? 0) }} / {{ (m.jobs?.last_24h?.failed ?? 0) }}</span></div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import api from '@/api/client'

const health = ref<any>({ services: [], latency: {} })
const m = ref<any>({})
const loading = ref(false)

function statusClass(s: string) {
  if (s === 'ok') return 'ok'
  if (s === 'degraded') return 'warn'
  return 'bad'
}
const allOk = computed(() => health.value.services.length > 0 && health.value.services.every((x: any) => x.status === 'ok'))
const bannerText = computed(() => {
  const sv = health.value.services
  if (!sv.length) return loading.value ? 'Checking services…' : 'No health data'
  const bad = sv.filter((x: any) => x.status !== 'ok').length
  return bad ? `${bad} of ${sv.length} services need attention` : `All ${sv.length} services are healthy`
})
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
.hm-banner { padding: 16px; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
.hm-ok { border-color: color-mix(in srgb, var(--ok) 40%, var(--border)); }
.hm-bad { border-color: color-mix(in srgb, var(--warn) 50%, var(--border)); }
.hm-ico { width: 38px; height: 38px; padding: 0; justify-content: center; border-radius: 10px; font-size: 18px; }
.hm-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.hm-d { font-size: 12.5px; color: var(--muted); }
</style>
