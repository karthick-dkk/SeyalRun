<template>
  <AppShell>
    <div class="page">
      <div class="ph">
        <div>
          <h1 class="h1">Posture hosts</h1>
          <p class="sub">Installed and pending packages, reboots and freshness for each Linux host.</p>
        </div>
        <div class="acts">
          <button class="btn ib" type="button" aria-label="Refresh" title="Refresh" @click="reload">↺</button>
          <button v-if="auth.isAdmin && !unavailable" class="btn p" type="button" :disabled="collectingAll" @click="collectAll">{{ collectingAll ? 'Collecting…' : 'Collect all' }}</button>
        </div>
      </div>

      <div v-if="unavailable" class="card"><div class="empty-state">
        <b>Posture service is not available</b>
        The posture service is not deployed or not reachable yet.
        <div style="margin-top:12px"><button class="btn" type="button" @click="reload">Retry</button></div>
      </div></div>

      <div v-else class="stack">
        <div v-if="error" class="callout" style="--c:var(--bad)" role="alert">{{ error }}</div>
        <div v-if="notice" class="callout" style="--c:var(--ok)" role="status">{{ notice }}</div>
        <section class="card">
          <div class="bar">
            <div class="srch" style="flex:1 1 240px;max-width:360px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg><label for="p-q" class="vh">Filter hosts</label><input id="p-q" v-model="q" class="in" placeholder="Filter by host or distro…" /></div>
            <div class="seg" role="group" aria-label="Show">
              <button v-for="f in FILTERS" :key="f.key" type="button" class="btn sm" :class="{ p: show === f.key }" :aria-pressed="show === f.key" @click="setShow(f.key)">{{ f.label }} <span class="cnt">{{ counts[f.key] }}</span></button>
            </div>
          </div>
          <div class="tw"><table class="t">
            <thead><tr><th>Host</th><th>Distro</th><th>Kernel</th><th class="r">Pending</th><th class="r">Security</th><th>Reboot</th><th>Collected</th><th>Status</th><th class="r"><span class="vh">Actions</span></th></tr></thead>
            <tbody>
              <tr v-for="h in filtered" :key="h.host_id" style="cursor:pointer" tabindex="0" @click="open(h)" @keydown.enter="open(h)">
                <td><span class="nm">{{ h.name || h.hostname }}</span><span v-if="h.name && h.hostname && h.name !== h.hostname" class="s2">{{ h.hostname }}</span></td>
                <td>{{ distroLabel(h) }}</td>
                <td class="mono">{{ h.kernel || '—' }}</td>
                <td class="r num" :title="updatesKnown(h) ? '' : 'The last scan could not list updates'">{{ updatesKnown(h) ? (h.pending_updates ?? '—') : '?' }}</td>
                <td class="r num"><span :style="updatesKnown(h) && h.security_updates ? 'color:var(--bad);font-weight:600' : ''">{{ updatesKnown(h) ? (h.security_updates ?? '—') : '?' }}</span></td>
                <td><span v-if="h.reboot_required" class="b warn">Reboot required</span><span v-else class="muted">—</span></td>
                <td class="muted">{{ h.collected_at ? ago(h.collected_at) : 'Never' }}</td>
                <td><span class="b" :class="statusKind(h.status)" :title="h.last_error || ''">{{ statusLabel(h.status) }}</span></td>
                <td class="r" @click.stop>
                  <span class="acts" style="justify-content:flex-end">
                    <button v-if="auth.isAdmin && h.reboot_required" class="btn sm" type="button" @click="rebootFor = h">Reboot</button>
                    <button v-if="auth.isAdmin" class="btn sm" type="button" :disabled="collecting[h.host_id]" @click="collectOne(h)">{{ collecting[h.host_id] ? 'Collecting…' : 'Collect now' }}</button>
                  </span>
                </td>
              </tr>
            </tbody>
          </table></div>
          <div v-if="loading" class="empty-state">Loading…</div>
          <div v-else-if="!filtered.length" class="empty-state"><b>No hosts to show</b>{{ hosts.length ? 'Nothing matches this filter.' : 'No host has reported facts yet. Use Collect all to gather them.' }}</div>
        </section>
      </div>
    </div>
    <RebootHostDialog v-if="rebootFor" :host-id="rebootFor.host_id" :hostname="rebootFor.name || rebootFor.hostname" @close="rebootFor = null" @queued="onRebootQueued" />
  </AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import RebootHostDialog from '@/components/posture/RebootHostDialog.vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { ago, distroLabel, isDown, detailOf, statusKind, statusLabel, updatesKnown, type PostureHost } from '@/components/posture/posture'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

type Show = 'all' | 'reboot' | 'security' | 'attention'
const FILTERS: { key: Show; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'reboot', label: 'Reboot required' },
  { key: 'security', label: 'Security updates' },
  { key: 'attention', label: 'Stale or failed' },
]
const show = ref<Show>((FILTERS.map((f) => f.key) as string[]).includes(String(route.query.show)) ? (route.query.show as Show) : 'all')
// The filter lives in the URL, so the dashboard can link straight to "reboot required".
function setShow(s: Show) {
  show.value = s
  const qy = { ...route.query }
  if (s === 'all') delete qy.show; else qy.show = s
  router.replace({ query: qy })
}

const hosts = ref<PostureHost[]>([])
const loading = ref(true)
const unavailable = ref(false)
const error = ref('')
const notice = ref('')
const q = ref('')
const collecting = reactive<Record<string, boolean>>({})
const collectingAll = ref(false)
const rebootFor = ref<PostureHost | null>(null)

const match: Record<Show, (h: PostureHost) => boolean> = {
  all: () => true,
  reboot: (h) => !!h.reboot_required,
  security: (h) => updatesKnown(h) && !!h.security_updates,
  attention: (h) => h.status === 'stale' || h.status === 'failed' || !updatesKnown(h),
}
const counts = computed(() => Object.fromEntries(FILTERS.map((f) => [f.key, hosts.value.filter(match[f.key]).length])) as Record<Show, number>)
const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  return hosts.value.filter(match[show.value]).filter((h) => !s || `${h.name || ''} ${h.hostname} ${h.distro} ${h.distro_version}`.toLowerCase().includes(s))
})

async function reload() {
  loading.value = true; error.value = ''; unavailable.value = false
  try { hosts.value = (await api.get('/posture/hosts')).data || [] }
  catch (e: any) { if (isDown(e)) unavailable.value = true; else error.value = detailOf(e, 'Could not load posture hosts.') }
  finally { loading.value = false }
}
onMounted(reload)

function open(h: PostureHost) { router.push(`/posture/hosts/${h.host_id}`) }
async function collectOne(h: PostureHost) {
  collecting[h.host_id] = true; error.value = ''
  try { await api.post(`/posture/hosts/${h.host_id}/collect`); await reload() }
  catch (e: any) { error.value = detailOf(e, 'Collection failed.') } finally { collecting[h.host_id] = false }
}
async function collectAll() {
  collectingAll.value = true; error.value = ''
  try { await api.post('/posture/collect'); await reload() }
  catch (e: any) { error.value = detailOf(e, 'Collection failed.') } finally { collectingAll.value = false }
}
function onRebootQueued(msg: string) { rebootFor.value = null; notice.value = msg }
</script>

<style scoped>
.seg { display: flex; gap: 6px; flex-wrap: wrap; }
tr[tabindex]:focus-visible { outline: 2px solid var(--link); outline-offset: -2px; }
</style>
