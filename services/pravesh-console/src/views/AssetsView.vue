<template>
  <AppShell>
    <div class="assets-page">
      <div class="assets-header">
        <div>
          <h2 class="page-title">Assets</h2>
          <p class="page-sub text-muted">All managed hosts from JumpServer · {{ assets.length }} total</p>
        </div>
        <div class="header-actions">
          <input v-model="search" class="prv-input" placeholder="Filter by name or IP…" />
          <div class="view-toggle">
            <button :class="{ active: viewMode === 'grid' }" @click="viewMode = 'grid'" title="Grid view">⊞</button>
            <button :class="{ active: viewMode === 'list' }" @click="viewMode = 'list'" title="List view">☰</button>
          </div>
          <button class="btn-refresh" @click="load" :disabled="loading" title="Refresh">↺</button>
        </div>
      </div>

      <div v-if="loading" class="loading">Loading assets from JumpServer…</div>

      <div v-else-if="filtered.length === 0" class="empty-state">
        <div class="empty-icon">⬡</div>
        <p>No assets found{{ search ? ` matching "${search}"` : '' }}.</p>
      </div>

      <!-- Grid view -->
      <div v-else-if="viewMode === 'grid'" class="assets-grid">
        <div v-for="a in filtered" :key="a.id" class="asset-card">
          <div class="asset-header">
            <div class="asset-status">
              <span class="status-dot" :class="a.is_active ? 'dot-ok' : 'dot-off'" />
              <span class="status-label" :class="a.is_active ? 'text-success' : 'text-muted'">
                {{ a.is_active ? 'Online' : 'Inactive' }}
              </span>
            </div>
            <div class="asset-btns">
              <button class="ssh-quick-btn" @click="openSSH(a)" title="Open SSH terminal">⊙ SSH</button>
              <button class="run-quick-btn" @click="openRun(a)" title="Run template">▶ Run</button>
            </div>
          </div>

          <div class="asset-body">
            <div class="asset-name">{{ a.name }}</div>
            <div class="asset-ip mono">{{ a.address }}</div>
            <div class="asset-platform text-muted" v-if="a.platform">{{ a.platform }}</div>
            <div class="asset-comment text-muted" v-if="a.comment">{{ a.comment }}</div>
          </div>

          <div class="asset-tags">
            <span v-if="a.domain_name" class="zone-badge" title="Network zone">
              🔗 {{ a.domain_name }}
            </span>
          </div>

          <div class="asset-footer">
            <button class="action-btn" @click="copyIP(a.address)">⎘ Copy IP</button>
          </div>
        </div>
      </div>

      <!-- List view -->
      <div v-else class="assets-list">
        <div class="list-header">
          <span>Status</span><span>Name</span><span>IP Address</span><span>Platform</span><span>Zone</span><span></span>
        </div>
        <div v-for="a in filtered" :key="a.id" class="list-row">
          <span>
            <span class="status-dot" :class="a.is_active ? 'dot-ok' : 'dot-off'" style="display:inline-block" />
          </span>
          <span class="row-name">{{ a.name }}</span>
          <span class="mono">{{ a.address }}</span>
          <span class="text-muted">{{ a.platform || '—' }}</span>
          <span>
            <span v-if="a.domain_name" class="zone-badge-sm">🔗 {{ a.domain_name }}</span>
            <span v-else class="text-subtle">—</span>
          </span>
          <span class="row-actions">
            <button class="action-btn" @click="copyIP(a.address)">⎘</button>
            <button class="ssh-row-btn" @click="openSSH(a)">⊙ SSH</button>
            <button class="run-quick-btn-sm" @click="openRun(a)">▶ Run</button>
          </span>
        </div>
      </div>

      <div class="assets-stats" v-if="!loading && assets.length">
        <span>{{ assets.filter(a => a.is_active).length }} active</span>
        <span>{{ assets.filter(a => !a.is_active).length }} inactive</span>
        <span v-if="assets.filter(a => a.domain_name).length">
          {{ assets.filter(a => a.domain_name).length }} in zones
        </span>
      </div>
    </div>

    <!-- SSH Launch Modal -->
    <SSHLaunchModal
      v-if="sshAsset"
      :asset="sshAsset"
      @close="sshAsset = null"
    />

    <!-- Quick run modal -->
    <div v-if="runAsset" class="modal-overlay" @click.self="runAsset = null">
      <div class="mini-modal">
        <div class="mm-header">
          <div>
            <h3>▶ Run on {{ runAsset.name }}</h3>
            <p class="mm-target">{{ runAsset.address }}
              <span v-if="runAsset.domain_name" class="zone-badge-sm">🔗 {{ runAsset.domain_name }}</span>
            </p>
          </div>
          <button @click="runAsset = null">✕</button>
        </div>
        <div class="mm-body">
          <label class="field-label">Template</label>
          <select v-model="selectedSlug" class="prv-select">
            <option value="">— pick a template —</option>
            <option v-for="t in templates" :key="t.slug" :value="t.slug">{{ t.name }}</option>
          </select>

          <!-- JMS accounts -->
          <template v-if="accountsLoading">
            <p class="hint-text">Loading accounts…</p>
          </template>
          <template v-else-if="jmsAccounts.length">
            <label class="field-label">JumpServer Account</label>
            <div class="acc-quick">
              <button
                v-for="acc in jmsAccounts"
                :key="acc.id"
                class="acc-quick-btn"
                :class="{ active: sshUser === acc.username }"
                @click="sshUser = acc.username"
              >{{ acc.privileged ? '⚡' : '👤' }} {{ acc.username }}</button>
            </div>
          </template>

          <label class="field-label">SSH Username</label>
          <input v-model="sshUser" class="prv-input" placeholder="test" />
          <label class="field-label">SSH Password</label>
          <input v-model="sshPassword" type="password" class="prv-input" placeholder="••••" />
          <label class="checkbox-row">
            <input type="checkbox" v-model="sshBecome" /> Use sudo (become)
          </label>

          <!-- Gateway section (auto-shown when asset has a zone with gateway) -->
          <template v-if="connectivity?.has_gateway">
            <div class="gateway-section">
              <div class="gw-header">
                <span class="gw-icon">🔗</span>
                <span class="gw-title">Gateway Required</span>
                <span class="gw-host">{{ connectivity.gateway?.host }}:{{ connectivity.gateway?.port }}</span>
              </div>
              <p class="hint-text">This host is in zone <strong>{{ connectivity.domain?.name }}</strong>. Ansible will connect via the gateway.</p>

              <div v-if="connectivity.gateway?.accounts?.length" class="acc-quick" style="margin-top:4px">
                <button
                  v-for="acc in connectivity.gateway.accounts"
                  :key="acc.username"
                  class="acc-quick-btn"
                  :class="{ active: gwUser === acc.username }"
                  @click="gwUser = acc.username"
                >{{ acc.privileged ? '⚡' : '👤' }} {{ acc.username }}</button>
              </div>

              <div class="creds-grid">
                <div class="field">
                  <label class="field-label">Gateway User</label>
                  <input v-model="gwUser" class="prv-input mono" :placeholder="connectivity.gateway?.accounts?.[0]?.username || 'gateway-user'" />
                </div>
                <div class="field">
                  <label class="field-label">Gateway Password</label>
                  <input v-model="gwPassword" type="password" class="prv-input" placeholder="••••" />
                </div>
              </div>
            </div>
          </template>
          <template v-else-if="connectivityLoading">
            <p class="hint-text">Checking gateway requirements…</p>
          </template>
        </div>

        <div class="mm-footer">
          <button class="btn-cancel" @click="runAsset = null">Cancel</button>
          <button class="btn-run" :disabled="!selectedSlug || runningDirect" @click="runDirect">
            {{ runningDirect ? '◌ Starting…' : '▶ Run Now' }}
          </button>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import SSHLaunchModal from '@/components/ssh/SSHLaunchModal.vue'
import { useEscapeKey } from '@/composables/useEscapeKey'
import { api } from '@/api/client'
import { templatesApi } from '@/api'
import { useUiStore } from '@/stores/ui'
import type { Template } from '@/types'

interface Asset { id: string; name: string; address: string; platform: string; is_active: boolean; comment: string; domain_id: string; domain_name: string }
interface JmsAccount { id: string; username: string; privileged: boolean; secret_type: string }
interface GatewayAccount { username: string; privileged: boolean; secret_type: string }
interface Connectivity {
  has_gateway: boolean
  domain: { id: string; name: string } | null
  gateway: { host: string; port: string; accounts: GatewayAccount[] } | null
}

const router = useRouter()
const ui = useUiStore()

const assets = ref<Asset[]>([])
const templates = ref<Template[]>([])
const loading = ref(true)
const search = ref('')
const viewMode = ref<'grid' | 'list'>('list')
const sshAsset = ref<Asset | null>(null)
const runAsset = ref<Asset | null>(null)

useEscapeKey(() => {
  if (sshAsset.value) { sshAsset.value = null; return }
  if (runAsset.value) { runAsset.value = null }
})
const selectedSlug = ref('')
const sshUser = ref('test')
const sshPassword = ref('test')
const sshBecome = ref(true)
const gwUser = ref('')
const gwPassword = ref('')
const runningDirect = ref(false)
const jmsAccounts = ref<JmsAccount[]>([])
const accountsLoading = ref(false)
const connectivity = ref<Connectivity | null>(null)
const connectivityLoading = ref(false)

const filtered = computed(() => {
  if (!search.value) return assets.value
  const q = search.value.toLowerCase()
  return assets.value.filter(a =>
    a.name.toLowerCase().includes(q) || a.address.includes(q) ||
    a.platform.toLowerCase().includes(q) || a.domain_name.toLowerCase().includes(q)
  )
})

async function load() {
  loading.value = true
  try {
    const resp = await api.get('/assets')
    assets.value = resp.data.items || []
  } catch {
    ui.error('Failed to load assets from JumpServer')
  } finally {
    loading.value = false
  }
}

function openSSH(a: Asset) {
  sshAsset.value = a
}

async function openRun(a: Asset) {
  runAsset.value = a
  selectedSlug.value = ''
  jmsAccounts.value = []
  connectivity.value = null
  gwUser.value = ''
  gwPassword.value = ''

  // Load accounts + connectivity in parallel
  accountsLoading.value = true
  connectivityLoading.value = true

  const [accResp, connResp] = await Promise.allSettled([
    api.get(`/assets/${a.id}/accounts`),
    api.get(`/assets/${a.id}/connectivity`),
  ])

  if (accResp.status === 'fulfilled') {
    jmsAccounts.value = accResp.value.data.accounts || []
    const privileged = jmsAccounts.value.find(acc => acc.privileged)
    const first = privileged || jmsAccounts.value[0]
    if (first) sshUser.value = first.username
  }
  accountsLoading.value = false

  if (connResp.status === 'fulfilled') {
    connectivity.value = connResp.value.data
    if (connectivity.value?.has_gateway && connectivity.value.gateway?.accounts?.length) {
      const gwa = connectivity.value.gateway.accounts.find(a => a.privileged) || connectivity.value.gateway.accounts[0]
      if (gwa) gwUser.value = gwa.username
    }
  }
  connectivityLoading.value = false
}

function copyIP(ip: string) {
  navigator.clipboard.writeText(ip)
  ui.toast(`Copied ${ip}`)
}

async function runDirect() {
  if (!runAsset.value || !selectedSlug.value) return
  runningDirect.value = true
  try {
    const allVars: Record<string, unknown> = { ansible_user: sshUser.value }
    if (sshPassword.value) {
      allVars['ansible_password'] = sshPassword.value
      allVars['ansible_become_password'] = sshPassword.value
    }
    if (sshBecome.value) allVars['ansible_become'] = true

    // Add gateway vars if applicable
    if (connectivity.value?.has_gateway && connectivity.value.gateway && gwUser.value) {
      allVars['_gateway_host'] = connectivity.value.gateway.host
      allVars['_gateway_port'] = connectivity.value.gateway.port || '22'
      allVars['_gateway_user'] = gwUser.value
      if (gwPassword.value) allVars['_gateway_password'] = gwPassword.value
    }

    const resp = await api.post(`/templates/${selectedSlug.value}/run`, {
      inventory_selector: runAsset.value.address,
      extra_vars: allVars,
    })
    ui.success(`Job started on ${runAsset.value.name}`)
    runAsset.value = null
    router.push(`/jobs/${resp.data.job_id}`)
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string } } }
    ui.error(axErr?.response?.data?.detail || 'Run failed')
  } finally {
    runningDirect.value = false
  }
}

onMounted(async () => {
  await load()
  const tresp = await templatesApi.list()
  templates.value = tresp.data.items
})
</script>

<style scoped>
.assets-page { display: flex; flex-direction: column; gap: var(--space-5); }

.assets-header { display: flex; justify-content: space-between; align-items: flex-start; }
.page-title { font-size: 20px; font-weight: 700; }
.page-sub { font-size: 12px; margin-top: 2px; }

.header-actions { display: flex; gap: var(--space-2); align-items: center; }
.prv-input { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; width: 220px; }
.prv-input:focus { border-color: var(--accent); }

.view-toggle {
  display: flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.view-toggle button {
  background: none;
  border: none;
  color: var(--text-muted);
  padding: var(--space-2) var(--space-3);
  font-size: 14px;
  cursor: pointer;
  transition: all var(--transition);
}
.view-toggle button.active { background: var(--accent-dim); color: var(--accent); }
.view-toggle button:hover:not(.active) { background: var(--bg-subtle); }

.btn-refresh { background: var(--bg-surface); border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); font-size: 16px; cursor: pointer; transition: all var(--transition); }
.btn-refresh:hover { color: var(--text); }

.loading, .empty-state { text-align: center; color: var(--text-muted); padding: var(--space-10); font-size: 13px; }
.empty-icon { font-size: 48px; color: var(--border-muted); margin-bottom: var(--space-4); }

/* Grid view */
.assets-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: var(--space-4); }

.asset-card { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); display: flex; flex-direction: column; overflow: hidden; transition: all var(--transition); }
.asset-card:hover { border-color: var(--border-muted); box-shadow: var(--shadow-sm); }

.asset-header { display: flex; justify-content: space-between; align-items: center; padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--border); background: var(--bg-overlay); }
.asset-status { display: flex; align-items: center; gap: var(--space-2); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: var(--success); box-shadow: 0 0 6px var(--success); }
.dot-off { background: var(--text-subtle); }
.status-label { font-size: 11px; font-weight: 600; }

.asset-btns { display: flex; gap: var(--space-1); }
.ssh-quick-btn { background: var(--accent-dim); color: var(--accent); border: 1px solid var(--accent); border-radius: var(--radius-md); padding: 2px 8px; font-size: 11px; font-weight: 700; cursor: pointer; transition: all var(--transition); }
.ssh-quick-btn:hover { background: var(--accent); color: #000; }
.run-quick-btn { background: var(--success-dim); color: var(--success); border: 1px solid var(--success); border-radius: var(--radius-md); padding: 2px 8px; font-size: 11px; font-weight: 700; cursor: pointer; transition: all var(--transition); }
.run-quick-btn:hover { background: var(--success); color: #000; }
.ssh-row-btn { padding: 2px 8px; border-radius: var(--radius-md); font-size: 11px; font-weight: 600; cursor: pointer; border: 1px solid var(--accent); color: var(--accent); background: none; white-space: nowrap; transition: all var(--transition); }
.ssh-row-btn:hover { background: var(--accent); color: #000; }

.asset-body { padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-1); flex: 1; }
.asset-name { font-size: 14px; font-weight: 600; color: var(--text); }
.asset-ip { font-size: 13px; color: var(--accent); }
.asset-platform, .asset-comment { font-size: 11px; }

.asset-tags { padding: 0 var(--space-4) var(--space-2); }
.zone-badge { display: inline-flex; align-items: center; gap: 4px; background: var(--accent-dim); color: var(--accent); font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: var(--radius-full); }
.zone-badge-sm { display: inline-flex; align-items: center; gap: 3px; background: var(--accent-dim); color: var(--accent); font-size: 10px; padding: 1px 6px; border-radius: var(--radius-full); }

.asset-footer { padding: var(--space-2) var(--space-4); border-top: 1px solid var(--border); }
.action-btn { background: none; border: none; color: var(--text-subtle); font-size: 11px; cursor: pointer; transition: color var(--transition); }
.action-btn:hover { color: var(--text-muted); }

/* List view */
.assets-list { display: flex; flex-direction: column; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; }

.list-header {
  display: grid;
  grid-template-columns: 60px 1fr 130px 120px 150px 120px;
  padding: var(--space-2) var(--space-4);
  background: var(--bg-overlay);
  font-size: 10px;
  font-weight: 700;
  color: var(--text-subtle);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border-bottom: 1px solid var(--border);
}

.list-row {
  display: grid;
  grid-template-columns: 60px 1fr 130px 120px 150px 120px;
  padding: var(--space-3) var(--space-4);
  align-items: center;
  border-top: 1px solid var(--border);
  font-size: 13px;
  transition: background var(--transition);
}
.list-row:first-of-type { border-top: none; }
.list-row:hover { background: var(--bg-overlay); }

.row-name { font-weight: 500; }
.mono { font-family: var(--font-mono); font-size: 12px; color: var(--accent); }
.text-muted { color: var(--text-muted); }
.text-subtle { color: var(--text-subtle); }

.row-actions { display: flex; gap: var(--space-2); align-items: center; }
.run-quick-btn-sm { background: var(--success-dim); color: var(--success); border: 1px solid var(--success); border-radius: var(--radius-md); padding: 2px 8px; font-size: 11px; font-weight: 700; cursor: pointer; white-space: nowrap; transition: all var(--transition); }
.run-quick-btn-sm:hover { background: var(--success); color: #000; }

.assets-stats { display: flex; gap: var(--space-5); font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); }

/* Modal */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 600; }
.mini-modal { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: var(--radius-xl); width: 480px; max-height: 85vh; display: flex; flex-direction: column; box-shadow: var(--shadow-lg); overflow: hidden; }
.mm-header { display: flex; align-items: flex-start; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); flex-shrink: 0; }
.mm-header h3 { font-size: 14px; font-weight: 600; }
.mm-target { font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); margin-top: 2px; display: flex; align-items: center; gap: var(--space-2); }
.mm-header > button { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; flex-shrink: 0; }
.mm-body { flex: 1; overflow-y: auto; padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-3); }
.field-label { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-top: var(--space-2); }
.hint-text { font-size: 11px; color: var(--text-subtle); font-style: italic; }
.prv-select { background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; width: 100%; }
.checkbox-row { display: flex; align-items: center; gap: var(--space-2); font-size: 12px; color: var(--text-muted); cursor: pointer; }

.acc-quick { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.acc-quick-btn { padding: var(--space-1) var(--space-2); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-overlay); color: var(--text-muted); font-size: 11px; font-family: var(--font-mono); cursor: pointer; transition: all var(--transition); }
.acc-quick-btn.active { border-color: var(--success); color: var(--success); background: var(--success-dim); }
.acc-quick-btn:hover:not(.active) { border-color: var(--border-muted); color: var(--text); }

/* Gateway section */
.gateway-section { background: var(--accent-dim); border: 1px solid var(--accent); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); margin-top: var(--space-2); }
.gw-header { display: flex; align-items: center; gap: var(--space-2); }
.gw-icon { font-size: 14px; }
.gw-title { font-size: 12px; font-weight: 700; color: var(--accent); }
.gw-host { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }

.creds-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.field { display: flex; flex-direction: column; gap: var(--space-1); }

.mm-footer { display: flex; justify-content: flex-end; gap: var(--space-3); padding: var(--space-4) var(--space-5); border-top: 1px solid var(--border); flex-shrink: 0; }
.btn-cancel { background: none; border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-4); font-size: 13px; cursor: pointer; }
.btn-run { background: var(--success); color: #000; border: none; border-radius: var(--radius-md); padding: var(--space-2) var(--space-5); font-size: 13px; font-weight: 700; cursor: pointer; }
.btn-run:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
