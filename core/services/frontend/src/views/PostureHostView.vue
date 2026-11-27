<template>
  <AppShell>
    <div class="page">
      <div><router-link class="lk" to="/posture/hosts">← Posture hosts</router-link></div>
      <div class="ph">
        <div>
          <h1 class="h1">{{ host ? (host.name || host.hostname) : 'Host' }}
            <span v-if="host" class="b" :class="statusKind(host.status)" style="vertical-align:middle;margin-left:8px;font-size:12px">{{ statusLabel(host.status) }}</span>
          </h1>
          <p class="sub">
            <template v-if="host">{{ distroLabel(host) }}<template v-if="host.kernel"> · <span class="mono">{{ host.kernel }}</span></template><template v-if="host.arch"> · {{ host.arch }}</template><template v-if="host.name && host.hostname && host.name !== host.hostname"> · {{ host.hostname }}</template></template>
          </p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="load">↺</button>
          <button v-if="auth.isAdmin && host" class="btn" type="button" :disabled="collecting" @click="collect">{{ collecting ? 'Collecting…' : 'Collect now' }}</button>
          <button v-if="auth.isAdmin && host" class="btn" :class="{ p: host.reboot_required }" type="button" @click="rebooting = true">Reboot</button>
        </div>
      </div>

      <div v-if="loading && !host" class="card"><div class="empty-state">Loading…</div></div>
      <div v-else-if="missing" class="card"><div class="empty-state"><b>Host not found</b>It does not exist or you cannot see it. <router-link class="lk" to="/posture/hosts">Back to the hosts</router-link></div></div>

      <div v-else-if="host" class="stack">
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>
        <div v-if="notice" class="callout" style="--c:var(--ok)" role="status">{{ notice }}</div>
        <div v-if="host.last_error" class="callout" style="--c:var(--bad)" role="alert">Last collection failed: {{ host.last_error }}</div>
        <div v-if="!updatesKnown(host)" class="callout" style="--c:var(--warn)" role="alert">The last scan could not list this host's updates, so the counts below are unknown. Collect again.</div>
        <div v-if="host.reboot_required" class="callout" style="--c:var(--warn)" role="status">
          <span>This host needs a reboot to finish applying updates<template v-if="host.reboot_method"> (detected by {{ host.reboot_method }})</template>.</span>
          <button v-if="auth.isAdmin" class="btn sm" type="button" style="margin-left:auto" @click="rebooting = true">Reboot now</button>
        </div>

        <section class="tiles" aria-label="Host summary">
          <div class="card stat"><span class="l">Pending updates</span><span class="v">{{ updatesKnown(host) ? (host.pending_updates ?? '—') : '?' }}</span><span class="d">Packages with a newer version</span></div>
          <div class="card stat"><span class="l">Security updates</span><span class="v" :style="updatesKnown(host) && host.security_updates ? 'color:var(--bad)' : ''">{{ updatesKnown(host) ? (host.security_updates ?? '—') : '?' }}</span><span class="d">Fix a security advisory</span></div>
          <div class="card stat"><span class="l">Open findings</span><span class="v" :style="findingsTotal ? 'color:var(--bad)' : ''">{{ findingsTotal ?? '—' }}</span><span class="d">Matched vulnerabilities</span></div>
          <div class="card stat"><span class="l">Reboot</span><span class="v" style="font-size:22px;line-height:1.6">{{ host.reboot_required ? 'Required' : 'Not required' }}</span><span class="d">{{ host.reboot_method ? `by ${host.reboot_method}` : 'No signal from the host' }}</span></div>
          <div class="card stat"><span class="l">Installed</span><span class="v">{{ host.packages_installed ?? '—' }}</span><span class="d">Packages</span></div>
          <div class="card stat"><span class="l">Collected</span><span class="v" style="font-size:22px;line-height:1.6">{{ host.collected_at ? ago(host.collected_at) : 'Never' }}</span><span class="d">{{ host.collected_at ? new Date(host.collected_at).toLocaleString() : 'Use Collect now' }}</span></div>
        </section>

        <div v-if="(host.warnings || []).length" class="callout" style="--c:var(--warn)">
          <span><b>Warnings from the last collection</b><br /><span v-for="w in host.warnings" :key="w" style="display:block">{{ w }}</span></span>
        </div>

        <div class="tabs" role="tablist">
          <button v-for="t in TABS" :key="t.key" role="tab" :aria-selected="tab === t.key" :class="['tab', { on: tab === t.key }]" @click="tab = t.key">{{ t.label }} <span class="cnt">{{ t.count() }}</span></button>
        </div>

        <!-- Findings -->
        <section v-if="tab === 'findings'" class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>CVE</th><th>Severity</th><th>Package</th><th>Installed</th><th>Fixed in</th><th class="r">Risk</th><th>Status</th></tr></thead>
            <tbody>
              <tr v-for="f in findings" :key="f.id">
                <td><span class="nm mono">{{ f.cve_id }}</span> <span v-if="f.kev" class="b bad" title="Listed in CISA Known Exploited Vulnerabilities">KEV</span></td>
                <td><span class="b" :class="sevKind(f.severity)">{{ cap(f.severity) }}</span></td>
                <td class="mono">{{ f.package }}</td>
                <td class="mono muted">{{ f.installed_version }}</td>
                <td class="mono">{{ f.fixed_version || '—' }}</td>
                <td class="r num"><b>{{ f.risk_score ?? '—' }}</b></td>
                <td><span class="b" :class="fStatusKind(f.status)">{{ cap(f.status) }}</span></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="!findings.length" class="empty-state"><b>No open findings</b>No vulnerability is matched to this host's packages.</div>
          <div v-else-if="(findingsTotal || 0) > findings.length" class="foot"><span>Showing the {{ findings.length }} highest-risk of {{ findingsTotal }}.</span><router-link class="lk" :to="{ path: '/posture', query: { host: hostId } }">All findings for this host</router-link></div>
        </section>

        <!-- Pending / installed packages -->
        <section v-else-if="tab === 'pending' || tab === 'installed'" class="card">
          <div class="bar"><div class="srch" style="flex:1 1 240px;max-width:360px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg><label for="ph-q" class="vh">Filter packages</label><input id="ph-q" v-model="pq" class="in" placeholder="Filter packages…" /></div>
            <label v-if="tab === 'pending'" class="row" style="gap:6px;font-size:13px"><input v-model="securityOnly" type="checkbox" class="chk" /> Security only</label>
          </div>
          <div class="tw" style="max-height:560px;overflow:auto">
            <table v-if="tab === 'pending'" class="t">
              <thead><tr><th>Package</th><th>Arch</th><th>Current</th><th>Available</th><th>From</th><th>Security</th></tr></thead>
              <tbody><tr v-for="p in pendingShown" :key="p.name + p.arch + p.available"><td class="mono">{{ p.name }}</td><td class="muted">{{ p.arch }}</td><td class="mono muted">{{ p.current }}</td><td class="mono">{{ p.available }}</td><td class="muted">{{ p.source }}</td><td><span v-if="p.security" class="b bad">Security</span></td></tr></tbody>
            </table>
            <table v-else class="t">
              <thead><tr><th>Package</th><th>Version</th><th>Arch</th></tr></thead>
              <tbody><tr v-for="p in installedShown" :key="p.name + p.arch"><td class="mono">{{ p.name }}</td><td class="mono muted">{{ p.version }}</td><td class="muted">{{ p.arch }}</td></tr></tbody>
            </table>
          </div>
          <div v-if="tab === 'pending' && !pendingShown.length" class="empty-state">{{ updatesKnown(host) ? (pq || securityOnly ? 'Nothing matches.' : 'Nothing pending.') : 'Unknown until the next successful collection.' }}</div>
          <div v-if="tab === 'installed' && !installedShown.length" class="empty-state">{{ pq ? 'Nothing matches.' : 'No package list collected.' }}</div>
        </section>

        <!-- Operations -->
        <section v-else class="card" style="padding:16px">
          <HostPackageOps :host-id="hostId" :hostname="host.name || host.hostname" />
        </section>
      </div>
    </div>
    <RebootHostDialog v-if="rebooting && host" :host-id="hostId" :hostname="host.name || host.hostname" @close="rebooting = false" @queued="onQueued" />
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import HostPackageOps from '@/components/patching/HostPackageOps.vue'
import RebootHostDialog from '@/components/posture/RebootHostDialog.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { ago, cap, detailOf, distroLabel, fStatusKind, sevKind, statusKind, statusLabel, updatesKnown } from '@/components/posture/posture'

const auth = useAuthStore()
const route = useRoute()
const hostId = computed(() => String(route.params.id))

const host = ref<any>(null)
const loading = ref(true)
const missing = ref(false)
const error = ref('')
const notice = ref('')
const collecting = ref(false)
const rebooting = ref(false)
const findings = ref<any[]>([])
const findingsTotal = ref<number | null>(null)

type Tab = 'findings' | 'pending' | 'installed' | 'ops'
const tab = ref<Tab>('pending')
const TABS: { key: Tab; label: string; count: () => number | string }[] = [
  { key: 'pending', label: 'Pending updates', count: () => (updatesKnown(host.value) ? (host.value?.packages_pending || []).length : '?') },
  { key: 'findings', label: 'Findings', count: () => findingsTotal.value ?? 0 },
  { key: 'installed', label: 'Installed packages', count: () => host.value?.packages_installed ?? 0 },
  { key: 'ops', label: 'Package operations', count: () => '' },
]

const pq = ref('')
const securityOnly = ref(false)
const pendingShown = computed(() => {
  const s = pq.value.trim().toLowerCase()
  return (host.value?.packages_pending || []).filter((p: any) => (!securityOnly.value || p.security) && (!s || p.name.toLowerCase().includes(s)))
})
const installedShown = computed(() => {
  const s = pq.value.trim().toLowerCase()
  return (host.value?.packages_installed_list || []).filter((p: any) => !s || p.name.toLowerCase().includes(s))
})

async function load() {
  loading.value = true; error.value = ''; missing.value = false
  try {
    host.value = (await api.get(`/posture/hosts/${hostId.value}`)).data
  } catch (e: any) {
    if ([403, 404].includes(e?.response?.status)) { missing.value = true; host.value = null }
    else error.value = detailOf(e, 'Could not load this host.')
  } finally { loading.value = false }
  try {
    const r = (await api.get('/posture/findings', { params: { host_id: hostId.value, status: 'open', sort: 'risk', limit: 50 } })).data
    findings.value = r.items || []; findingsTotal.value = r.total ?? findings.value.length
  } catch { findings.value = []; findingsTotal.value = null }
}
onMounted(load)
watch(hostId, () => { tab.value = 'pending'; load() })

async function collect() {
  collecting.value = true; error.value = ''
  try { await api.post(`/posture/hosts/${hostId.value}/collect`); await load() }
  catch (e: any) { error.value = detailOf(e, 'Collection failed.') } finally { collecting.value = false }
}
function onQueued(msg: string) { rebooting.value = false; notice.value = msg; tab.value = 'ops' }
</script>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.stat .d { font-size: 12.5px; color: var(--muted); }
.callout { display: flex; align-items: center; gap: 12px; }
</style>
