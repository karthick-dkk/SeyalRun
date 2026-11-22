<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Posture</h1>
          <p class="sub">What is installed, what is pending and what needs a reboot across your Linux hosts.</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="reload">↺</button>
          <button v-if="tab === 'hosts' && auth.isAdmin && !unavailable" class="btn p" type="button" :disabled="collectingAll" @click="collectAll">{{ collectingAll ? 'Collecting…' : 'Collect all' }}</button>
          <label v-if="tab === 'feeds' && auth.isAdmin && !unavailable" class="btn p" :class="{ 'is-dis': uploading }" style="cursor:pointer">
            {{ uploading ? 'Uploading…' : 'Import feed bundle' }}
            <input type="file" class="vh" aria-label="Feed bundle file" :disabled="uploading" @change="onFile" />
          </label>
        </div>
      </div>

      <div v-if="unavailable" class="card"><div class="empty-state">
        <b>Posture service is not available</b>
        The posture service is not deployed or not reachable yet. Once it is running, host facts and feed bundles appear here.
        <div style="margin-top:12px"><button class="btn" type="button" @click="reload">Retry</button></div>
      </div></div>

      <div v-else class="stack">
        <section class="tiles" aria-label="Summary">
          <div class="card stat"><span class="l">Hosts reporting</span><span class="v">{{ summary ? `${summary.hosts_reporting} / ${summary.hosts_total}` : '—' }}</span><span class="d">Facts collected at least once</span></div>
          <div class="card stat"><span class="l">Security updates</span><span class="v">{{ summary?.security_updates ?? '—' }}</span><span class="d">Pending across all hosts</span></div>
          <div class="card stat"><span class="l">Reboot required</span><span class="v">{{ summary?.reboot_required ?? '—' }}</span><span class="d">Hosts waiting on a restart</span></div>
          <div class="card stat"><span class="l">Active feed</span><span class="v" style="font-size:20px;line-height:1.5">{{ summary?.active_feed_version || 'None' }}</span><span class="d">{{ summary?.feed_age_days != null ? `${summary.feed_age_days} day${summary.feed_age_days === 1 ? '' : 's'} old` : 'No bundle activated' }}</span></div>
        </section>

        <div class="tabs" role="tablist">
          <button role="tab" :aria-selected="tab === 'hosts'" :class="['tab', { on: tab === 'hosts' }]" @click="tab = 'hosts'">Hosts <span class="cnt">{{ hosts.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'feeds'" :class="['tab', { on: tab === 'feeds' }]" @click="tab = 'feeds'">Feed bundles <span class="cnt">{{ feeds.length }}</span></button>
        </div>
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>

        <!-- Hosts -->
        <section v-if="tab === 'hosts'" class="card">
          <div class="bar"><div class="srch" style="flex:1 1 240px;max-width:360px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg><label for="p-q" class="vh">Filter hosts</label><input id="p-q" v-model="q" class="in" placeholder="Filter by host or distro…" /></div></div>
          <div class="tw"><table class="t">
            <thead><tr><th>Host</th><th>Distro</th><th>Kernel</th><th class="r">Pending</th><th class="r">Security</th><th>Reboot</th><th>Collected</th><th>Status</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="h in filteredHosts" :key="h.host_id" style="cursor:pointer" tabindex="0" @click="openHost(h)" @keydown.enter="openHost(h)">
                <td><span class="nm">{{ h.hostname }}</span></td>
                <td>{{ distroLabel(h) }}</td>
                <td class="mono">{{ h.kernel || '—' }}</td>
                <td class="r num">{{ h.pending_updates ?? '—' }}</td>
                <td class="r num"><span :style="h.security_updates ? 'color:var(--bad);font-weight:600' : ''">{{ h.security_updates ?? '—' }}</span></td>
                <td><span v-if="h.reboot_required" class="b warn">Reboot required</span><span v-else class="muted">—</span></td>
                <td class="muted">{{ h.collected_at ? ago(h.collected_at) : 'Never' }}</td>
                <td><span class="b" :class="statusKind(h.status)" :title="h.last_error || ''">{{ statusLabel(h.status) }}</span></td>
                <td class="r" @click.stop><button v-if="auth.isAdmin" class="btn sm" type="button" :disabled="collecting[h.host_id]" @click="collectOne(h)">{{ collecting[h.host_id] ? 'Collecting…' : 'Collect now' }}</button></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!filteredHosts.length" class="empty-state"><b>No hosts to show</b>{{ hosts.length ? 'Nothing matches this filter.' : 'No host has reported facts yet. Use Collect all to gather them.' }}</div>
        </section>

        <!-- Feeds -->
        <section v-else class="card">
          <div class="tw"><table class="t">
            <thead><tr><th>Version</th><th>Generated</th><th>Imported</th><th>Signer</th><th>Sources</th><th>State</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="f in feeds" :key="f.id">
                <td class="mono">{{ f.version }}</td>
                <td class="muted">{{ f.generated_at ? new Date(f.generated_at).toLocaleDateString() : '—' }}</td>
                <td><span class="muted">{{ f.imported_at ? new Date(f.imported_at).toLocaleString() : '—' }}</span><span class="s2">{{ f.imported_by || '' }}</span></td>
                <td class="mono" style="font-size:12px">{{ f.signed_by_key || '—' }}</td>
                <td><span v-for="s in f.sources" :key="s.name" class="tag" style="margin-right:4px">{{ s.name }} {{ s.entries.toLocaleString() }}</span></td>
                <td><span v-if="f.active" class="b ok">Active</span><span v-else class="b off">Inactive</span></td>
                <td class="r"><button v-if="auth.isAdmin && !f.active" class="btn sm" type="button" :disabled="activating === f.id" @click="activate(f)">{{ activating === f.id ? 'Activating…' : 'Activate' }}</button></td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="!feeds.length && !loading" class="empty-state"><b>No feed bundles</b>Import a signed bundle to enable vulnerability matching.</div>
        </section>
      </div>
    </div>

    <!-- Host detail drawer -->
    <div v-if="detail" class="modal-overlay" @click.self="detail = null">
      <div class="modal" role="dialog" :aria-label="`Posture for ${detail.hostname}`" style="width:min(720px,96vw)">
        <div class="modal-header"><span>{{ detail.hostname }}<span class="muted" style="font-weight:400;margin-left:8px">{{ distroLabel(detail) }}</span></span><button class="btn sm ib g" type="button" aria-label="Close" @click="detail = null">×</button></div>
        <div class="modal-body" style="display:flex;flex-direction:column;gap:16px">
          <dl class="kv">
            <dt>Kernel</dt><dd class="mono">{{ detail.kernel || '—' }}</dd>
            <dt>Architecture</dt><dd>{{ detail.arch || '—' }}</dd>
            <dt>Installed</dt><dd>{{ detail.packages_installed ?? '—' }} packages</dd>
            <dt>Pending</dt><dd>{{ detail.pending_updates ?? '—' }} updates · {{ detail.security_updates ?? '—' }} security</dd>
            <dt>Reboot</dt><dd>{{ detail.reboot_required ? 'Required' : 'Not required' }}</dd>
            <dt>Collected</dt><dd>{{ detail.collected_at ? new Date(detail.collected_at).toLocaleString() : 'Never' }}</dd>
            <dt>Status</dt><dd><span class="b" :class="statusKind(detail.status)">{{ statusLabel(detail.status) }}</span></dd>
          </dl>
          <div v-if="detail.last_error" class="callout" style="--c:var(--bad)" role="alert">{{ detail.last_error }}</div>
          <div>
            <h3 class="ct" style="margin-bottom:8px">Pending packages <span class="cnt">{{ (detail.packages_pending || []).length }}</span></h3>
            <div class="card tw" style="max-height:220px;overflow:auto"><table class="t">
              <thead><tr><th>Package</th><th>Current</th><th>Available</th><th>Security</th></tr></thead>
              <tbody><tr v-for="p in (detail.packages_pending || [])" :key="p.name"><td class="mono">{{ p.name }}</td><td class="mono muted">{{ p.current }}</td><td class="mono">{{ p.available }}</td><td><span v-if="p.security" class="b bad">Security</span></td></tr></tbody>
            </table><div v-if="!(detail.packages_pending || []).length" class="empty-state">Nothing pending.</div></div>
          </div>
          <div>
            <h3 class="ct" style="margin-bottom:8px">Installed packages <span class="cnt">{{ (detail.packages_installed_list || []).length }}</span></h3>
            <div class="card tw" style="max-height:240px;overflow:auto"><table class="t">
              <thead><tr><th>Package</th><th>Version</th><th>Arch</th></tr></thead>
              <tbody><tr v-for="p in (detail.packages_installed_list || [])" :key="p.name + p.arch"><td class="mono">{{ p.name }}</td><td class="mono muted">{{ p.version }}</td><td class="muted">{{ p.arch }}</td></tr></tbody>
            </table><div v-if="!(detail.packages_installed_list || []).length" class="empty-state">No package list collected.</div></div>
          </div>
        </div>
        <div class="modal-footer">
          <button v-if="auth.isAdmin" class="btn" type="button" :disabled="collecting[detail.host_id]" @click="collectOne(detail)">{{ collecting[detail.host_id] ? 'Collecting…' : 'Collect now' }}</button>
          <button class="btn p" type="button" @click="detail = null">Close</button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

interface PostureHost { host_id: string; hostname: string; os_family?: string; distro?: string; distro_version?: string; kernel?: string; arch?: string; packages_installed?: number; pending_updates?: number; security_updates?: number; reboot_required?: boolean; collected_at?: string | null; status: 'ok' | 'stale' | 'failed' | 'never'; last_error?: string | null }
interface Feed { id: string; version: string; generated_at?: string; imported_at?: string; imported_by?: string; signed_by_key?: string; active: boolean; sources: { name: string; entries: number }[] }

const auth = useAuthStore()
const tab = ref<'hosts' | 'feeds'>('hosts')
const hosts = ref<PostureHost[]>([])
const feeds = ref<Feed[]>([])
const summary = ref<any>(null)
const loading = ref(true)
const unavailable = ref(false)
const error = ref('')
const q = ref('')
const detail = ref<any>(null)
const collecting = reactive<Record<string, boolean>>({})
const collectingAll = ref(false)
const uploading = ref(false)
const activating = ref('')

// 404 / 502 / 503 mean the posture service is not deployed (or not up) yet: friendly empty state.
const isDown = (e: any) => [404, 502, 503, 504].includes(e?.response?.status) || (!e?.response && !!e?.request)
const detailOf = (e: any, fallback: string) => e?.response?.data?.detail || fallback

async function reload() {
  loading.value = true; error.value = ''; unavailable.value = false
  try {
    const [s, h, f] = await Promise.all([api.get('/posture/summary'), api.get('/posture/hosts'), api.get('/posture/feeds')])
    summary.value = s.data; hosts.value = h.data || []; feeds.value = f.data || []
  } catch (e: any) {
    if (isDown(e)) unavailable.value = true
    else error.value = detailOf(e, 'Could not load posture data.')
  } finally { loading.value = false }
}
onMounted(reload)

const filteredHosts = computed(() => {
  const s = q.value.trim().toLowerCase()
  return s ? hosts.value.filter((h) => `${h.hostname} ${h.distro} ${h.distro_version}`.toLowerCase().includes(s)) : hosts.value
})
const distroLabel = (h: any) => [h.distro, h.distro_version].filter(Boolean).join(' ') || h.os_family || '—'
const statusKind = (s: string) => ({ ok: 'ok', stale: 'warn', failed: 'bad', never: 'off' } as Record<string, string>)[s] || 'off'
const statusLabel = (s: string) => ({ ok: 'Fresh', stale: 'Stale', failed: 'Failed', never: 'Never collected' } as Record<string, string>)[s] || s
function ago(iso: string) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86400)} d ago`
}

async function openHost(h: PostureHost) {
  detail.value = { ...h }
  try { detail.value = (await api.get(`/posture/hosts/${h.host_id}`)).data }
  catch (e: any) { error.value = detailOf(e, 'Could not load host detail.') }
}
async function collectOne(h: { host_id: string }) {
  collecting[h.host_id] = true; error.value = ''
  try { await api.post(`/posture/hosts/${h.host_id}/collect`); await reload(); if (detail.value?.host_id === h.host_id) detail.value = (await api.get(`/posture/hosts/${h.host_id}`)).data }
  catch (e: any) { error.value = detailOf(e, 'Collection failed.') } finally { collecting[h.host_id] = false }
}
async function collectAll() {
  collectingAll.value = true; error.value = ''
  try { await api.post('/posture/collect'); await reload() }
  catch (e: any) { error.value = detailOf(e, 'Collection failed.') } finally { collectingAll.value = false }
}
async function onFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploading.value = true; error.value = ''
  try {
    const fd = new FormData(); fd.append('file', file)
    await api.post('/posture/feeds/import', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
    await reload()
  } catch (e: any) { error.value = detailOf(e, 'Import failed.') }   // 400 detail shown verbatim
  finally { uploading.value = false; input.value = '' }
}
async function activate(f: Feed) {
  activating.value = f.id; error.value = ''
  try { await api.post(`/posture/feeds/${f.id}/activate`); await reload() }
  catch (e: any) { error.value = detailOf(e, 'Activation failed.') } finally { activating.value = '' }
}
</script>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.stat .d { font-size: 12.5px; color: var(--muted); }
.is-dis { opacity: .6; pointer-events: none; }
tr[tabindex]:focus-visible { outline: 2px solid var(--link); outline-offset: -2px; }
</style>
