<template>
  <AppShell>
  <div class="hosts-view">
    <div class="hv-header">
      <h1 class="hv-title">Hosts <span class="hv-count">{{ filteredHosts.length }}</span></h1>
      <div class="hv-search">
        <input v-model="filter" type="text" placeholder="Filter by name or IP…" class="hv-input" />
      </div>
      <button class="hv-refresh" @click="loadAll" title="Refresh">
        <svg style="width:14px;height:14px;display:block" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>
      </button>
    </div>

    <div v-if="loading && !hosts.length" class="hv-loading">Loading hosts…</div>
    <div v-else-if="error" class="hv-error">{{ error }}</div>
    <div v-else class="hv-table-wrap">
      <table class="hv-table">
        <thead>
          <tr>
            <th class="col-status"></th>
            <th class="col-name hv-th-sort" @click="toggleSort('name')">Host <span class="hv-sort-arrow" v-if="sortKey==='name'">{{ sortDir===1?'▲':'▼' }}</span></th>
            <th class="col-ip hv-th-sort" @click="toggleSort('ip')">IP <span class="hv-sort-arrow" v-if="sortKey==='ip'">{{ sortDir===1?'▲':'▼' }}</span></th>
            <th class="col-port">Port</th>
            <th class="col-gw">Gateway</th>
            <th class="col-sessions">Sessions</th>
            <th v-if="isAdmin" class="col-creds">Credentials</th>
            <th v-if="isAdmin" class="col-users">Users</th>
            <th class="col-actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="host in filteredHosts" :key="host.id">
            <tr class="hv-row" :class="{ 'hv-row--expanded': expandedHost === host.id, 'hv-row--disabled': !host.enabled }">
              <td class="col-status">
                <span class="hv-dot" :class="reachClass(host)" :title="reachTitle(host)"></span>
              </td>
              <td class="col-name">
                <span class="hv-name-text" :title="host.name">{{ host.name }}</span>
                <span v-if="!host.enabled" class="hv-disabled-badge">off</span>
              </td>
              <td class="col-ip hv-mono" :title="host.ip">{{ host.ip }}</td>
              <td class="col-port hv-mono">{{ host.port || 22 }}</td>
              <td class="col-gw">
                <span v-if="zoneGateway(host.zone_id)" class="hv-gw-cell">
                  <span class="hv-dot" :class="gwDotClass(host.zone_id)" :title="gwHint(host.zone_id)"></span>
                  <span class="hv-gw-name" :title="gwHint(host.zone_id)">{{ zoneGateway(host.zone_id)!.name }}</span>
                  <button class="hv-btn hv-gw-ping"
                    :class="{ 'is-spin': gwPending.has(zoneGateway(host.zone_id)!.id), 'is-ok': gwResults[zoneGateway(host.zone_id)!.id] === true, 'is-fail': gwResults[zoneGateway(host.zone_id)!.id] === false }"
                    :title="gwHint(host.zone_id)"
                    @click.stop="testGateway(host.zone_id)">▶</button>
                </span>
                <span v-else class="hv-none">—</span>
              </td>
              <td class="col-sessions">
                <router-link v-if="sessionCounts[host.id]" :to="`/sessions?host_id=${host.id}`" class="hv-sess-count" :title="`${sessionCounts[host.id]} session(s)`">{{ sessionCounts[host.id] }}</router-link>
                <span v-else class="hv-none">—</span>
              </td>
              <td v-if="isAdmin" class="col-creds">
                <span v-if="hostCreds(host.id).length" class="hv-tags">
                  <span v-for="c in hostCreds(host.id)" :key="c.id" class="hv-tag hv-tag-cred" :title="c.name">{{ c.username }}</span>
                </span>
                <span v-else class="hv-none">—</span>
              </td>
              <td v-if="isAdmin" class="col-users">
                <span v-if="hostUsers(host.id).length" class="hv-tags">
                  <span v-for="uid in hostUsers(host.id)" :key="uid" class="hv-tag hv-tag-user">{{ userMap[uid] || uid.slice(0, 8) }}</span>
                </span>
                <span v-else class="hv-none">—</span>
              </td>
              <td class="col-actions hv-actions">
                <button class="hv-btn hv-btn-terminal" title="Open SSH Terminal" @click="openTerminal(host)">💻</button>
                <button
                  class="hv-btn hv-btn-test"
                  :class="{ 'is-ok': pingResults[host.id] === true, 'is-fail': pingResults[host.id] === false, 'is-spin': pingPending.has(host.id) }"
                  :title="pingHint(host)"
                  @click="testHost(host)"
                >▶</button>
              </td>
            </tr>
          </template>
          <tr v-if="!loading && !filteredHosts.length">
            <td :colspan="isAdmin ? 9 : 7" class="hv-empty">{{ filter ? 'No hosts match the filter' : 'No hosts available for your account' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Edit Host Modal (admin only) -->
    <div v-if="editModal.visible" class="modal-overlay" @click.self="editModal.visible = false">
      <div class="modal" @click.stop>
        <div class="modal-header">Edit Host</div>
        <form class="hv-form" @submit.prevent="saveHost">
          <label class="hv-label">Name<input v-model="editModal.form.name" class="hv-field" required /></label>
          <label class="hv-label">IP Address<input v-model="editModal.form.ip" class="hv-field" required /></label>
          <label class="hv-label">SSH Port<input v-model.number="editModal.form.port" class="hv-field" type="number" min="1" max="65535" /></label>
          <label class="hv-label">OS Type<input v-model="editModal.form.os_type" class="hv-field" placeholder="linux / windows / macos" /></label>
          <label class="hv-label hv-chk-row">
            <input type="checkbox" v-model="editModal.form.enabled" />
            <span>Enabled</span>
          </label>
          <div v-if="editModal.error" class="hv-modal-err">{{ editModal.error }}</div>
          <div class="modal-footer" style="border-top:none;padding:4px 0 0">
            <button type="button" class="hv-cancel" @click="editModal.visible = false">Cancel</button>
            <button type="submit" class="hv-save" :disabled="editModal.saving">
              {{ editModal.saving ? 'Saving…' : 'Save Changes' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import api, { terminalUrl } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const isAdmin = computed(() => auth.isAdmin)

// ── State ─────────────────────────────────────────────────────────────────

const hosts = ref<any[]>([])
const zones = ref<any[]>([])
const gateways = ref<any[]>([])
const credentials = ref<any[]>([])
const authorizations = ref<any[]>([])
const userMap = reactive<Record<string, string>>({})

const loading = ref(false)
const error = ref('')
const filter = ref('')

const pingResults = reactive<Record<string, boolean>>({})
const pingPending = reactive(new Set<string>())

const gwResults = reactive<Record<string, boolean | null>>({})
const gwPending = reactive(new Set<string>())

const expandedHost = ref<string | null>(null)
const hostSessions = reactive<Record<string, any[]>>({})
const hostSessionsLoading = reactive<Record<string, boolean>>({})
const sessionCounts = reactive<Record<string, number>>({})

const editModal = reactive({
  visible: false,
  hostId: '',
  saving: false,
  error: '',
  form: { name: '', ip: '', port: 22, os_type: '', enabled: true, group_ids: [] as string[], zone_id: null as string | null, zabbix_hostid: null as string | null },
})

// ── Computed ──────────────────────────────────────────────────────────────

const sortKey = ref<'name' | 'ip' | ''>('')
const sortDir = ref<1 | -1>(1)
function toggleSort(key: 'name' | 'ip') {
  if (sortKey.value === key) { sortDir.value = sortDir.value === 1 ? -1 : 1 }
  else { sortKey.value = key; sortDir.value = 1 }
}

const filteredHosts = computed(() => {
  const q = filter.value.toLowerCase()
  let rows = !q ? hosts.value : hosts.value.filter(h => h.name?.toLowerCase().includes(q) || h.ip?.includes(q))
  if (sortKey.value) {
    const key = sortKey.value
    rows = [...rows].sort((a, b) => (a[key] || '').localeCompare(b[key] || '', undefined, { numeric: true }) * sortDir.value)
  }
  return rows
})

// ── Data helpers ──────────────────────────────────────────────────────────

function zoneName(zoneId: string | null): string {
  if (!zoneId) return '—'
  return zones.value.find(z => z.id === zoneId)?.name ?? '—'
}

function zoneGateway(zoneId: string | null): any | null {
  if (!zoneId) return null
  return gateways.value.find(gw => gw.zone_id === zoneId) ?? null
}

function gwDotClass(zoneId: string | null): string {
  const gw = zoneGateway(zoneId)
  if (!gw) return 'dot-dim'
  const result = gwResults[gw.id]
  if (result === true) return 'dot-green'
  if (result === false) return 'dot-red'
  return gw.enabled !== false ? 'dot-dim' : 'dot-red'
}

function gwHint(zoneId: string | null): string {
  const gw = zoneGateway(zoneId)
  if (!gw) return 'No gateway'
  if (gwPending.has(gw.id)) return 'Testing…'
  const result = gwResults[gw.id]
  const addr = `${gw.host || gw.ip || ''}:${gw.port || 22}`
  if (result === true) return `Gateway reachable — ${gw.name} (${addr})`
  if (result === false) return `Gateway unreachable — ${gw.name} (${addr})`
  return `${gw.name} (${addr}) — click ▶ to test`
}

function hostCreds(hostId: string): any[] {
  return credentials.value.filter(c => c.host_ids?.includes(hostId))
}

function hostUsers(hostId: string): string[] {
  const ids = new Set<string>()
  for (const a of authorizations.value) {
    if (a.host_id === hostId && a.user_id) ids.add(a.user_id)
  }
  return [...ids]
}

function reachClass(host: any): string {
  if (host.is_reachable === true) return 'dot-green'
  if (host.is_reachable === false) return 'dot-red'
  return 'dot-dim'
}

function reachTitle(host: any): string {
  if (host.is_reachable === true) return `Reachable — last tested ${fmtTime(host.last_ping_at)}`
  if (host.is_reachable === false) return `Unreachable — last tested ${fmtTime(host.last_ping_at)}`
  return 'Reachability unknown — click ▶ to test'
}

function fmtTime(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleString()
}

function pingHint(host: any): string {
  if (pingPending.has(host.id)) return 'Testing SSH port…'
  if (host.id in pingResults) return pingResults[host.id] ? `Reachable (${host.ip}:${host.port || 22})` : `Unreachable (${host.ip}:${host.port || 22})`
  return `Test SSH reachability (${host.ip}:${host.port || 22})`
}

// ── Load data ─────────────────────────────────────────────────────────────

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    // /hosts is the one call this page cannot function without — fetch it directly so
    // its failure surfaces the real error. zones/credentials/authorizations are only
    // used for admin-tier auxiliary columns (already hidden for non-admin/support via
    // isAdminOrSupport) and a role that legitimately can't see them (e.g. `user`) must
    // still get their host list — so those three degrade to an empty array on failure
    // instead of rejecting the whole batch and blanking the page.
    const hostsR = await api.get('/hosts')
    const [zonesR, credsR, authzR] = await Promise.all([
      api.get('/zones').catch(() => ({ data: [] })),
      api.get('/credentials').catch(() => ({ data: [] })),
      api.get('/authorizations').catch(() => ({ data: [] })),
    ])
    hosts.value = hostsR.data
    zones.value = zonesR.data
    credentials.value = credsR.data
    authorizations.value = authzR.data

    // Session counts per host (non-blocking) for the Sessions column.
    api.get('/ssh/sessions').then(r => {
      for (const k of Object.keys(sessionCounts)) delete sessionCounts[k]
      for (const sess of (r.data || [])) {
        if (sess.host_id) sessionCounts[sess.host_id] = (sessionCounts[sess.host_id] || 0) + 1
      }
    }).catch(() => { /* sessions unavailable for this role */ })

    // Build user_id → username map
    const unknownIds = new Set<string>()
    for (const a of authzR.data) {
      if (a.user_id && !userMap[a.user_id]) unknownIds.add(a.user_id)
    }
    if (unknownIds.size > 0) {
      try {
        const usersR = await api.get('/users')
        for (const u of usersR.data) userMap[u.id] = u.username
      } catch { /* non-critical */ }
    }

    // Load gateways: try list response first, then fetch zone details for used zones
    const allGws: any[] = []
    for (const z of zonesR.data as any[]) {
      if (Array.isArray(z.gateways) && z.gateways.length) {
        for (const gw of z.gateways) allGws.push({ ...gw, zone_id: z.id })
      }
    }
    if (allGws.length === 0) {
      const usedZoneIds = [...new Set<string>(
        (hostsR.data as any[]).map(h => h.zone_id).filter(Boolean)
      )]
      await Promise.all(usedZoneIds.map(async (zid: string) => {
        try {
          const zr = await api.get(`/zones/${zid}`)
          for (const gw of zr.data.gateways || []) allGws.push({ ...gw, zone_id: zid })
        } catch { /* zone may have no gateways */ }
      }))
    }
    gateways.value = allGws
  } catch (e: any) {
    error.value = e.response?.data?.detail ?? 'Failed to load hosts'
  } finally {
    loading.value = false
  }
}

// ── Actions ───────────────────────────────────────────────────────────────

function openTerminal(host: any) {
  window.open(terminalUrl(`host_id=${host.id}&autoconnect=1`), '_blank')
}

async function toggleSessionHistory(hostId: string) {
  if (expandedHost.value === hostId) {
    expandedHost.value = null
    return
  }
  expandedHost.value = hostId
  if (hostSessions[hostId]) return
  hostSessionsLoading[hostId] = true
  try {
    const resp = await api.get('/ssh/sessions', { params: { host_id: hostId } })
    const sessions = resp.data.slice(0, 10)
    try {
      const recResp = await api.get('/recordings', { params: { host_id: hostId } })
      const recMap: Record<string, string> = {}
      for (const r of recResp.data) recMap[r.session_id] = r.id
      for (const s of sessions) s.recording_id = recMap[s.id] || null
    } catch { /* non-critical */ }
    hostSessions[hostId] = sessions
  } catch { hostSessions[hostId] = [] } finally {
    hostSessionsLoading[hostId] = false
  }
}

function calcDuration(s: any): string {
  if (!s.started_at) return '—'
  const end = s.ended_at ? new Date(s.ended_at).getTime() : Date.now()
  const sec = Math.round((end - new Date(s.started_at).getTime()) / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

async function testHost(host: any) {
  if (pingPending.has(host.id)) return
  pingPending.add(host.id)
  delete pingResults[host.id]
  try {
    const resp = await api.post(`/hosts/${host.id}/test`)
    pingResults[host.id] = resp.data.reachable
    const h = hosts.value.find(x => x.id === host.id)
    if (h) { h.is_reachable = resp.data.reachable; h.last_ping_at = resp.data.tested_at }
  } catch {
    pingResults[host.id] = false
  } finally {
    pingPending.delete(host.id)
  }
}

async function testGateway(zoneId: string | null) {
  const gw = zoneGateway(zoneId)
  if (!gw || gwPending.has(gw.id)) return
  gwPending.add(gw.id)
  try {
    const resp = await api.post(`/zones/${gw.zone_id}/gateways/${gw.id}/test`)
    gwResults[gw.id] = resp.data.reachable ?? resp.data.ok ?? true
  } catch {
    gwResults[gw.id] = false
  } finally {
    gwPending.delete(gw.id)
  }
}

function openEdit(host: any) {
  editModal.hostId = host.id
  editModal.error = ''
  editModal.saving = false
  editModal.form = {
    name: host.name,
    ip: host.ip,
    port: host.port || 22,
    os_type: host.os_type || '',
    enabled: host.enabled !== false,
    group_ids: host.group_ids || [],
    zone_id: host.zone_id || null,
    zabbix_hostid: host.zabbix_hostid || null,
  }
  editModal.visible = true
}

async function saveHost() {
  editModal.saving = true
  editModal.error = ''
  try {
    const resp = await api.put(`/hosts/${editModal.hostId}`, editModal.form)
    const idx = hosts.value.findIndex(h => h.id === editModal.hostId)
    if (idx !== -1) hosts.value[idx] = resp.data
    editModal.visible = false
  } catch (e: any) {
    editModal.error = e.response?.data?.detail ?? 'Save failed'
  } finally {
    editModal.saving = false
  }
}

onMounted(() => { loadAll() })
</script>

<style scoped>
.hosts-view {
  padding: 24px 28px;
  min-height: 100%;
  background: var(--bg);
  color: var(--text);
}

/* ── Header ─────────────────────────────────────────────────────────────── */
.hv-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.hv-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
}
.hv-count {
  display: inline-block;
  background: var(--border);
  color: var(--text2);
  font-size: 13px;
  font-weight: 400;
  padding: 1px 7px;
  border-radius: 10px;
  margin-left: 6px;
}
.hv-search { flex: 1; max-width: 320px; }
.hv-input {
  width: 100%;
  box-sizing: border-box;
  padding: 6px 10px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.hv-input:focus { border-color: var(--accent2); }
.hv-refresh {
  background: none;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text2);
  padding: 5px 8px;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  transition: color 0.2s;
}
.hv-refresh:hover { color: var(--accent2); }

/* ── Loading / Error ────────────────────────────────────────────────────── */
.hv-loading { color: var(--text2); padding: 32px; text-align: center; }
.hv-error { color: var(--danger); padding: 16px; background: var(--bg3); border-radius: 6px; }

/* ── Table ──────────────────────────────────────────────────────────────── */
.hv-table-wrap { overflow-x: auto; }
.hv-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.hv-table th {
  text-align: left;
  padding: 8px 12px;
  background: var(--bg3);
  color: var(--text2);
  font-weight: 500;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.hv-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.hv-row:hover td { background: var(--bg3); }
.hv-row--disabled td { opacity: 0.55; }

/* ── Sortable column headers ───────────────────────────────────────────── */
.hv-th-sort { cursor: pointer; user-select: none; }
.hv-th-sort:hover { color: var(--text); }
.hv-sort-arrow { font-size: 9px; margin-left: 3px; color: var(--accent2); }

/* ── Column widths ─────────────────────────────────────────────────────── */
.col-status { width: 24px; padding: 0 8px !important; }
.col-name   { width: 180px; max-width: 180px; }
.col-ip     { width: 130px; max-width: 130px; }
.col-port   { width: 54px; }
.col-gw       { min-width: 160px; }
.col-sessions { min-width: 90px; }
.hv-sess-count { display: inline-flex; align-items: center; justify-content: center; min-width: 26px; padding: 1px 8px; border-radius: 99px; background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.35); color: var(--accent2); font-size: 12px; font-weight: 700; text-decoration: none; }
.hv-sess-count:hover { background: rgba(88,166,255,0.24); }
.col-creds  { min-width: 110px; }
.col-users  { min-width: 90px; }
.col-actions { width: 96px; }

/* ── Name cell ─────────────────────────────────────────────────────────── */
.col-name { display: table-cell; }
.hv-name-text {
  display: inline-block;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  font-weight: 500;
}
.hv-disabled-badge {
  display: inline-block;
  margin-left: 5px;
  padding: 1px 5px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 600;
  background: rgba(239,68,68,0.18);
  color: var(--text2);
  vertical-align: middle;
  text-transform: uppercase;
}

/* ── IP cell ───────────────────────────────────────────────────────────── */
.hv-mono {
  font-family: monospace;
  color: var(--text2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 130px;
}

/* ── Gateway cell ──────────────────────────────────────────────────────── */
.hv-gw-cell {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.hv-gw-name {
  font-size: 12px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 90px;
  display: inline-block;
  vertical-align: middle;
}
.hv-gw-ping {
  padding: 2px 5px !important;
  font-size: 9px !important;
  flex-shrink: 0;
}

.hv-none { color: var(--text2); }
.hv-empty { text-align: center; color: var(--text2); padding: 32px; }

/* ── Dots ───────────────────────────────────────────────────────────────── */
.hv-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-green { background: var(--accent); box-shadow: 0 0 4px var(--accent); }
.dot-red   { background: var(--danger); }
.dot-dim   { background: var(--border); }

/* ── Tags ───────────────────────────────────────────────────────────────── */
.hv-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.hv-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 10px;
  font-size: 11px;
  white-space: nowrap;
}
.hv-tag-cred { background: rgba(59,130,246,0.18); color: var(--accent2); }
.hv-tag-user { background: rgba(34,197,94,0.18); color: var(--accent); }

/* ── Action buttons ─────────────────────────────────────────────────────── */
.hv-actions { display: flex; gap: 5px; }
.hv-btn {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--bg3);
  color: var(--text2);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
  line-height: 1;
  white-space: nowrap;
}
.hv-btn:hover { border-color: var(--text2); color: var(--text); }
.hv-btn-terminal:hover { border-color: var(--accent2); color: var(--accent2); }
.hv-btn-test:hover     { border-color: var(--accent); color: var(--accent); }
.hv-btn-edit:hover     { border-color: var(--warn); color: var(--warn); }
.hv-btn-test.is-ok, .hv-gw-ping.is-ok   { color: var(--accent); border-color: var(--accent); }
.hv-btn-test.is-fail, .hv-gw-ping.is-fail { color: var(--danger); border-color: var(--danger); }
.hv-btn-test.is-spin, .hv-gw-ping.is-spin { opacity: 0.5; }
.hv-gw-ping:hover { border-color: var(--accent2); color: var(--accent2); }

/* ── Edit Modal ─────────────────────────────────────────────────────────── */
.hv-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.hv-modal {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 8px;
  width: 420px;
  padding: 0;
  overflow: hidden;
}
.hv-modal-head {
  padding: 14px 18px;
  font-weight: 600;
  font-size: 15px;
  border-bottom: 1px solid var(--border);
}
.hv-form {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hv-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text2);
  font-weight: 500;
}
.hv-field {
  padding: 7px 10px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.hv-field:focus { border-color: var(--accent2); }
.hv-chk-row { flex-direction: row; align-items: center; gap: 8px; }
.hv-modal-err { color: var(--danger); font-size: 12px; }
.hv-modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding-top: 4px; }
.hv-cancel {
  padding: 7px 14px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text2);
  cursor: pointer;
  font-size: 13px;
}
.hv-cancel:hover { border-color: var(--text2); color: var(--text); }
.hv-save {
  padding: 7px 16px;
  background: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 5px;
  color: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
}
.hv-save:disabled { opacity: 0.5; cursor: not-allowed; }
.hv-save:not(:disabled):hover { filter: brightness(1.1); }

/* ── Expand button ──────────────────────────────────────────────────────── */
.hv-expand-btn {
  background: none; border: none; color: var(--text2); cursor: pointer;
  font-size: 11px; padding: 0 4px 0 0; vertical-align: middle;
  transition: color 0.15s;
}
.hv-expand-btn:hover { color: var(--accent2); }
.hv-row--expanded td { background: var(--bg2); }

/* ── Session history panel ──────────────────────────────────────────────── */
.hv-sessions-row td { padding: 0; border-bottom: 1px solid var(--border); }
.hv-sessions-cell { background: var(--bg2); }
.hv-sessions-panel { padding: 12px 20px 14px 40px; }
.hv-sessions-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text2); font-weight: 500;
}
.hv-sessions-all { color: var(--accent2); text-decoration: none; font-size: 11px; }
.hv-sessions-all:hover { text-decoration: underline; }
.hv-sessions-loading, .hv-sessions-empty { color: var(--text2); font-size: 12px; }
.hv-sess-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.hv-sess-table th {
  text-align: left; padding: 4px 10px; color: var(--text2);
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
}
.hv-sess-table td { padding: 5px 10px; border-bottom: 1px solid var(--border); color: var(--text2); }
.hv-sess-badge {
  display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 10px;
  font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
}
.hv-sess-badge--active     { background: rgba(34,197,94,0.18); color: var(--accent); }
.hv-sess-badge--closed     { background: var(--bg3); color: var(--text2); }
.hv-sess-badge--terminated { background: rgba(239,68,68,0.18); color: var(--danger); }
.hv-sess-badge--pending    { background: rgba(59,130,246,0.18); color: var(--accent2); }
.hv-sess-badge--error      { background: rgba(210,153,34,0.2); color: var(--warn); }
.hv-btn-play { text-decoration: none; }
</style>
