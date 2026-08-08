<template>
  <div>
    <!-- ── Zones ──────────────────────────────────────────────────────────── -->
    <div class="card">
      <div class="card-header">
        Zones
        <button class="btn btn-primary btn-sm" @click="openCreateZone">+ Zone</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Parent Zone</th><th>Description</th><th>Gateways</th><th>Created</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="z in zones" :key="z.id">
            <td style="font-weight:600"><span class="zone-badge"><span class="zone-badge-icon">⊕</span>{{ z.name }}</span></td>
            <td style="color:var(--text2);font-size:12px">{{ zoneName(z.parent_zone_id) }}</td>
            <td style="color:var(--text2)">{{ z.description || '—' }}</td>
            <td style="color:var(--text2);font-size:12px">{{ gatewayCount(z.id) }}</td>
            <td style="color:var(--text2);font-size:12px">{{ formatDate(z.created_at) }}</td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" @click="openCreateGateway(z.id)">+ Gateway</button>
                <button class="btn-pill btn-pill-outline" @click="openEditZone(z)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeZone(z)">🗑 Delete</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!zones.length && !loading" style="padding:32px;text-align:center;color:var(--text2)">No zones yet.</div>
      <div v-if="loading" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Gateways (shown by default, below zones) ───────────────────────── -->
    <div class="card" style="margin-top:16px">
      <div class="card-header">
        Gateways
        <button class="btn btn-primary btn-sm" :disabled="!zones.length" @click="openCreateGateway()">+ Gateway</button>
      </div>
      <table class="table">
        <thead>
          <tr><th>Name</th><th>Zone</th><th>Host</th><th>Port</th><th>Username</th><th>Credential</th><th>Health</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="g in allGateways" :key="g.id">
            <td style="font-weight:600">{{ g.name }}</td>
            <td><span class="zone-badge"><span class="zone-badge-icon">⊕</span>{{ g.zone_name }}</span></td>
            <td><span class="ip-mono">{{ g.host }}</span></td>
            <td>{{ g.port }}</td>
            <td>{{ g.username || '—' }}</td>
            <td style="color:var(--text2);font-size:12px">{{ credentialName(g.credential_id) }}</td>
            <td>
              <span v-if="testResults[g.id]?.status === 'testing'" class="gw-health gw-health--testing">Testing…</span>
              <span v-else-if="testResults[g.id]?.status === 'ok'" class="gw-health gw-health--ok">● Reachable · {{ testResults[g.id].latency }}ms</span>
              <span v-else-if="testResults[g.id]?.status === 'fail'" class="gw-health gw-health--fail" :title="testResults[g.id].error">● Unreachable</span>
              <span v-else style="color:var(--text2);font-size:12px">—</span>
            </td>
            <td>
              <div style="display:flex;gap:8px;justify-content:flex-end">
                <button class="btn-pill btn-pill-outline" :disabled="testResults[g.id]?.status === 'testing'" @click="testGateway(g)">⚡ Test</button>
                <button class="btn-pill btn-pill-outline" @click="openEditGateway(g)">✎ Edit</button>
                <button class="btn-pill btn-pill-outline" style="color:var(--danger);border-color:var(--danger)" @click="removeGateway(g)">🗑</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!allGateways.length && !loadingGateways" style="padding:32px;text-align:center;color:var(--text2)">
        {{ zones.length ? 'No gateways yet — add one with “+ Gateway”.' : 'Create a zone first, then add gateways.' }}
      </div>
      <div v-if="loadingGateways" style="padding:32px;text-align:center;color:var(--text2)">Loading…</div>
    </div>

    <!-- ── Create / Edit Zone ─────────────────────────────────────────────── -->
    <div v-if="showZoneModal" class="modal-overlay" @click.self="closeZoneModal">
      <div class="modal">
        <div class="modal-header">{{ editingZone ? `Edit Zone — ${editingZone.name}` : 'Add Zone' }}<button class="btn btn-sm btn-icon" @click="closeZoneModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group"><label class="form-label">Name</label><input v-model="zoneForm.name" class="input" placeholder="e.g. dc-east" /></div>
          <div class="form-group"><label class="form-label">Description</label><input v-model="zoneForm.description" class="input" placeholder="Optional description" /></div>
          <div class="form-group">
            <label class="form-label">Parent Zone <span style="color:var(--text2);font-weight:400">(optional — for multi-hop ProxyJump chains)</span></label>
            <select v-model="zoneForm.parent_zone_id" class="input">
              <option value="">— No Parent (direct) —</option>
              <option v-for="z in parentZoneOptions" :key="z.id" :value="z.id">{{ z.name }}</option>
            </select>
          </div>
          <div v-if="zoneError" style="color:var(--danger);font-size:12px">{{ zoneError }}</div>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="closeZoneModal">Cancel</button>
          <button class="btn btn-primary" @click="saveZone" :disabled="savingZone">{{ savingZone ? 'Saving…' : (editingZone ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>

    <!-- ── Create / Edit Gateway (right-side panel, like Assets) ──────────── -->
    <div v-if="showGatewayModal" class="modal-overlay" @click.self="closeGatewayModal">
      <div class="modal">
        <div class="modal-header">{{ editingGateway ? `Edit Gateway — ${editingGateway.name}` : 'Add Gateway' }}<button class="btn btn-sm btn-icon" @click="closeGatewayModal">✕</button></div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">Zone</label>
            <select v-model="gatewayForm.zone_id" class="input" :disabled="!!editingGateway">
              <option value="">— Select zone —</option>
              <option v-for="z in zones" :key="z.id" :value="z.id">{{ z.name }}</option>
            </select>
          </div>
          <div class="form-grid-2">
            <div class="form-group"><label class="form-label">Name</label><input v-model="gatewayForm.name" class="input" placeholder="e.g. bastion-1" /></div>
            <div class="form-group"><label class="form-label">Host</label><input v-model="gatewayForm.host" class="input" placeholder="10.0.0.1" /></div>
            <div class="form-group"><label class="form-label">SSH Port</label><input v-model.number="gatewayForm.port" type="number" min="1" max="65535" class="input" /></div>
            <div class="form-group"><label class="form-label">Username</label><input v-model="gatewayForm.username" class="input" placeholder="e.g. jump" /></div>
          </div>

          <!-- Credential — pick existing or create inline (same as Assets) -->
          <div class="section-head">
            <label class="form-label" style="margin-bottom:0">SSH Credential</label>
            <button class="btn-pill" :class="newCred.show ? 'btn-pill-active' : 'btn-pill-outline'" style="font-size:11px" @click="newCred.show ? resetNewCred() : (newCred.show = true)">{{ newCred.show ? '✕ Cancel' : '+ New Credential' }}</button>
          </div>

          <div v-if="newCred.show" class="inline-cred-form">
            <div class="form-grid-2">
              <div class="form-group"><label class="form-label">Name</label><input v-model="newCred.name" class="input" placeholder="optional label" /></div>
              <div class="form-group"><label class="form-label">Username</label><input v-model="newCred.username" class="input" placeholder="e.g. jump" autocomplete="off" /></div>
              <div class="form-group"><label class="form-label">Type</label><select v-model="newCred.secret_type" class="input"><option value="password">Password</option><option value="ssh_key">SSH Key</option></select></div>
              <div v-if="newCred.secret_type === 'password'" class="form-group"><label class="form-label">Password</label><input v-model="newCred.password" type="password" class="input" autocomplete="new-password" /></div>
            </div>
            <template v-if="newCred.secret_type === 'ssh_key'">
              <div class="form-group"><label class="form-label">Private Key</label><textarea v-model="newCred.private_key" class="input" rows="4" style="font-family:var(--font-mono);font-size:11px" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></div>
              <div class="form-group"><label class="form-label">Passphrase <span style="color:var(--text2);font-weight:400">(optional)</span></label><input v-model="newCred.passphrase" type="password" class="input" autocomplete="new-password" /></div>
            </template>
            <div v-if="newCred.error" style="color:var(--danger);font-size:12px">{{ newCred.error }}</div>
            <div style="display:flex;gap:8px;justify-content:flex-end">
              <button class="btn btn-sm" @click="resetNewCred">Cancel</button>
              <button class="btn btn-sm btn-primary" :disabled="newCred.saving" @click="createInlineCred">{{ newCred.saving ? 'Creating…' : 'Create & Use' }}</button>
            </div>
          </div>

          <div class="form-group">
            <select v-model="gatewayForm.credential_id" class="input">
              <option value="">— No Credential —</option>
              <option v-for="c in credentials" :key="c.id" :value="c.id">{{ c.name || c.username }}{{ c.username && c.name ? ` (${c.username})` : '' }}</option>
            </select>
          </div>

          <div v-if="gatewayError" style="color:var(--danger);font-size:12px">{{ gatewayError }}</div>
        </div>
        <div class="modal-footer">
          <button v-if="editingGateway" class="btn" :disabled="testResults[editingGateway.id]?.status === 'testing'" @click="testGateway(editingGateway)" style="margin-right:auto">⚡ Test</button>
          <button class="btn" @click="closeGatewayModal">Cancel</button>
          <button class="btn btn-primary" @click="saveGateway" :disabled="savingGateway">{{ savingGateway ? 'Saving…' : (editingGateway ? 'Save' : 'Create') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import api from '@/api/client'
import { useConfirm } from '@/composables/useConfirm'

const { confirm } = useConfirm()

const zones = ref<any[]>([])
const credentials = ref<any[]>([])
const allGateways = ref<any[]>([])
const loading = ref(false)
const loadingGateways = ref(false)

const credentialById = computed(() => new Map(credentials.value.map(c => [c.id, c])))
function credentialName(id: string | null) {
  if (!id) return '—'
  const c = credentialById.value.get(id)
  return c ? (c.name || c.username) : id
}
function gatewayCount(zoneId: string) {
  return allGateways.value.filter(g => g.zone_id === zoneId).length
}
function formatDate(d: string) { return new Date(d).toLocaleDateString() }
const zoneById = computed(() => new Map(zones.value.map(z => [z.id, z])))
function zoneName(id: string | null) {
  if (!id) return '—'
  return zoneById.value.get(id)?.name || id
}
// Excludes the zone being edited itself — full cycle prevention (ruling out its
// descendants too) is enforced server-side and surfaced via zoneError on save.
const parentZoneOptions = computed(() => zones.value.filter(z => z.id !== editingZone.value?.id))

async function loadZones() {
  loading.value = true
  try {
    const { data } = await api.get('/zones')
    zones.value = data
  } finally {
    loading.value = false
  }
  await loadAllGateways()
}
async function loadCredentials() {
  const { data } = await api.get('/credentials')
  credentials.value = data
}
// Fetch every zone's gateways and flatten into one list (tagged with zone).
async function loadAllGateways() {
  loadingGateways.value = true
  try {
    const lists = await Promise.all(zones.value.map(async (z) => {
      try {
        const { data } = await api.get(`/zones/${z.id}/gateways`)
        return data.map((g: any) => ({ ...g, zone_id: z.id, zone_name: z.name }))
      } catch { return [] }
    }))
    allGateways.value = lists.flat()
  } finally {
    loadingGateways.value = false
  }
}

// ── Create / Edit Zone ────────────────────────────────────────────────────
const showZoneModal = ref(false)
const editingZone = ref<any>(null)
const zoneForm = reactive({ name: '', description: '', parent_zone_id: '' })
const zoneError = ref('')
const savingZone = ref(false)

function openCreateZone() {
  editingZone.value = null
  Object.assign(zoneForm, { name: '', description: '', parent_zone_id: '' })
  zoneError.value = ''
  showZoneModal.value = true
}
function openEditZone(z: any) {
  editingZone.value = z
  Object.assign(zoneForm, { name: z.name, description: z.description, parent_zone_id: z.parent_zone_id || '' })
  zoneError.value = ''
  showZoneModal.value = true
}
function closeZoneModal() {
  showZoneModal.value = false
  editingZone.value = null
}

async function saveZone() {
  savingZone.value = true
  zoneError.value = ''
  try {
    const payload = { name: zoneForm.name, description: zoneForm.description, parent_zone_id: zoneForm.parent_zone_id || null }
    if (editingZone.value) {
      await api.put(`/zones/${editingZone.value.id}`, payload)
    } else {
      await api.post('/zones', payload)
    }
    closeZoneModal()
    loadZones()
  } catch (e: any) {
    zoneError.value = e?.response?.data?.detail || 'Failed to save zone'
  } finally {
    savingZone.value = false
  }
}

async function removeZone(z: any) {
  if (!await confirm(`Delete zone "${z.name}"? Any gateways in this zone will also be removed.`, { title: 'Delete Zone', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/zones/${z.id}`)
    loadZones()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete zone')
  }
}

// ── Create / Edit Gateway ──────────────────────────────────────────────────
const showGatewayModal = ref(false)
const editingGateway = ref<any>(null)
const gatewayForm = reactive({ zone_id: '', name: '', host: '', port: 22, username: '', credential_id: '' })
const gatewayError = ref('')
const savingGateway = ref(false)

// Inline credential creation — secret is sent once, never echoed back.
const newCred = reactive({ show: false, name: '', username: '', secret_type: 'password', password: '', private_key: '', passphrase: '', saving: false, error: '' })
function resetNewCred() { Object.assign(newCred, { show: false, name: '', username: '', secret_type: 'password', password: '', private_key: '', passphrase: '', saving: false, error: '' }) }
async function createInlineCred() {
  newCred.error = ''
  if (!newCred.username.trim()) { newCred.error = 'Username is required.'; return }
  if (newCred.secret_type === 'password' && !newCred.password) { newCred.error = 'Password is required.'; return }
  if (newCred.secret_type === 'ssh_key' && !newCred.private_key.trim()) { newCred.error = 'Private key is required.'; return }
  newCred.saving = true
  try {
    const secret = newCred.secret_type === 'password'
      ? { password: newCred.password }
      : { private_key: newCred.private_key, passphrase: newCred.passphrase }
    const res = await api.post('/credentials', {
      name: newCred.name.trim() || newCred.username.trim(),
      username: newCred.username.trim(),
      secret_type: newCred.secret_type,
      secret,
      host_ids: [],
    })
    credentials.value.push(res.data)
    gatewayForm.credential_id = res.data.id
    resetNewCred()
  } catch (e: any) {
    newCred.error = e?.response?.data?.detail || 'Failed to create credential'
  } finally {
    newCred.saving = false
  }
}

function openCreateGateway(zoneId = '') {
  editingGateway.value = null
  Object.assign(gatewayForm, { zone_id: zoneId || (zones.value[0]?.id ?? ''), name: '', host: '', port: 22, username: '', credential_id: '' })
  resetNewCred()
  gatewayError.value = ''
  showGatewayModal.value = true
}
function openEditGateway(g: any) {
  editingGateway.value = g
  Object.assign(gatewayForm, { zone_id: g.zone_id, name: g.name, host: g.host, port: g.port, username: g.username || '', credential_id: g.credential_id || '' })
  resetNewCred()
  gatewayError.value = ''
  showGatewayModal.value = true
}
function closeGatewayModal() {
  showGatewayModal.value = false
  editingGateway.value = null
  resetNewCred()
}

async function saveGateway() {
  if (!gatewayForm.zone_id) { gatewayError.value = 'Select a zone.'; return }
  savingGateway.value = true
  gatewayError.value = ''
  try {
    const payload = {
      name: gatewayForm.name,
      host: gatewayForm.host,
      port: gatewayForm.port,
      username: gatewayForm.username,
      credential_id: gatewayForm.credential_id || null,
    }
    if (editingGateway.value) {
      await api.put(`/zones/${editingGateway.value.zone_id}/gateways/${editingGateway.value.id}`, payload)
    } else {
      await api.post(`/zones/${gatewayForm.zone_id}/gateways`, payload)
    }
    closeGatewayModal()
    loadAllGateways()
  } catch (e: any) {
    gatewayError.value = e?.response?.data?.detail || 'Failed to save gateway'
  } finally {
    savingGateway.value = false
  }
}

async function removeGateway(g: any) {
  if (!await confirm(`Delete gateway "${g.name}"?`, { title: 'Delete Gateway', danger: true, confirmLabel: 'Delete' })) return
  try {
    await api.delete(`/zones/${g.zone_id}/gateways/${g.id}`)
    loadAllGateways()
  } catch (e: any) {
    alert(e?.response?.data?.detail || 'Failed to delete gateway')
  }
}

// ── Gateway health test ────────────────────────────────────────────────────
const testResults = reactive<Record<string, { status: 'testing' | 'ok' | 'fail'; latency?: number; error?: string }>>({})
async function testGateway(g: any) {
  testResults[g.id] = { status: 'testing' }
  try {
    const { data } = await api.post(`/zones/${g.zone_id}/gateways/${g.id}/test`)
    testResults[g.id] = data.reachable
      ? { status: 'ok', latency: data.latency_ms }
      : { status: 'fail', error: data.error || 'Unreachable' }
  } catch (e: any) {
    testResults[g.id] = { status: 'fail', error: e?.response?.data?.detail || 'Test failed' }
  }
}

onMounted(() => {
  loadZones()
  loadCredentials()
})
</script>

<style scoped>
.form-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 480px) { .form-grid-2 { grid-template-columns: 1fr; } }

.section-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 4px 0 8px; }
.btn-pill-active { background: rgba(88,166,255,0.16); border-color: rgba(88,166,255,0.45); color: #58a6ff; }
.btn-pill-active:hover { background: rgba(88,166,255,0.26); }

.inline-cred-form {
  background: var(--bg3); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 9px;
}

.gw-health { font-size: 12px; font-weight: 600; white-space: nowrap; }
.gw-health--ok { color: var(--accent); }
.gw-health--fail { color: var(--danger); cursor: help; }
.gw-health--testing { color: var(--text2); }
</style>
