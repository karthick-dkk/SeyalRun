<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title-row">
          <span class="run-icon">▶</span>
          <h3>Run Template</h3>
          <span class="template-name">{{ template.name }}</span>
        </div>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">

        <!-- Step 1: Target Hosts -->
        <div class="section">
          <label class="section-label">① Target Hosts</label>

          <div v-if="assetsLoading" class="hint-text">Loading hosts from JumpServer…</div>
          <div v-else-if="assets.length" class="asset-grid">
            <button
              v-for="a in assets"
              :key="a.id"
              class="asset-btn"
              :class="{ active: selectedAsset?.id === a.id }"
              @click="selectAsset(a)"
              :title="a.name"
            >
              <span class="asset-dot" :class="a.is_active ? 'dot-ok' : 'dot-off'" />
              <span class="asset-ip">{{ a.address }}</span>
              <span class="asset-name">{{ a.name }}</span>
            </button>
          </div>

          <div class="quick-targets">
            <button class="qt-btn" :class="{ active: inventory === 'all' && !selectedAsset }" @click="selectedAsset = null; inventory = 'all'">All hosts</button>
            <button class="qt-btn" :class="{ active: inventory === 'localhost' && !selectedAsset }" @click="selectedAsset = null; inventory = 'localhost'">Localhost</button>
          </div>

          <div class="field-row">
            <label class="field-label">Inventory / IP / Group</label>
            <input v-model="inventory" class="prv-input mono" placeholder="192.168.64.2  or  webservers  or  all" />
          </div>
        </div>

        <!-- Step 2: Credentials -->
        <div class="section">
          <label class="section-label">② SSH Credentials</label>

          <!-- JumpServer accounts for selected asset -->
          <div v-if="selectedAsset && jmsAccounts.length" class="jms-accounts">
            <label class="field-label">JumpServer Stored Accounts</label>
            <div class="account-list">
              <button
                v-for="acc in jmsAccounts"
                :key="acc.id"
                class="account-btn"
                :class="{ active: sshUser === acc.username }"
                @click="sshUser = acc.username"
                :title="`${acc.privileged ? 'Privileged' : 'Standard'} account`"
              >
                <span class="acc-icon">{{ acc.privileged ? '⚡' : '👤' }}</span>
                <span class="acc-user">{{ acc.username }}</span>
                <span class="acc-type">{{ acc.secret_type }}</span>
              </button>
            </div>
            <p class="hint-text">Select an account to pre-fill username. Password must be entered below.</p>
          </div>
          <div v-else-if="selectedAsset && accountsLoading" class="hint-text">Loading accounts…</div>

          <div class="creds-grid">
            <div class="field">
              <label class="field-label">Username</label>
              <input v-model="sshUser" class="prv-input mono" placeholder="test" />
            </div>
            <div class="field">
              <label class="field-label">Password</label>
              <input v-model="sshPassword" type="password" class="prv-input" placeholder="••••" autocomplete="off" />
            </div>
          </div>

          <label class="checkbox-row">
            <input type="checkbox" v-model="sshBecome" />
            <span>Use <code>sudo</code> (become) — required for most system tasks</span>
          </label>

          <!-- Gateway section (shown when selected asset is in a zone) -->
          <template v-if="connectivityLoading">
            <p class="hint-text">Checking gateway requirements…</p>
          </template>
          <template v-else-if="connectivity?.has_gateway">
            <div class="gateway-section">
              <div class="gw-header">
                <span>🔗</span>
                <span class="gw-title">Gateway Required — {{ connectivity.domain?.name }}</span>
                <span class="gw-host">{{ connectivity.gateway?.host }}:{{ connectivity.gateway?.port }}</span>
              </div>
              <p class="hint-text">Ansible will connect through this bastion host using ProxyCommand.</p>
              <div v-if="connectivity.gateway?.accounts?.length" class="acc-quick-sm">
                <button
                  v-for="acc in connectivity.gateway.accounts"
                  :key="acc.username"
                  class="account-btn"
                  :class="{ active: gwUser === acc.username }"
                  @click="gwUser = acc.username"
                >{{ acc.privileged ? '⚡' : '👤' }} {{ acc.username }}</button>
              </div>
              <div class="creds-grid">
                <div class="field">
                  <label class="field-label">Gateway Username</label>
                  <input v-model="gwUser" class="prv-input mono" placeholder="gateway-user" />
                </div>
                <div class="field">
                  <label class="field-label">Gateway Password</label>
                  <input v-model="gwPassword" type="password" class="prv-input" placeholder="••••" />
                </div>
              </div>
            </div>
          </template>
        </div>

        <!-- Step 3: Required variables (only if template has them) -->
        <div class="section" v-if="template.required_vars?.length">
          <label class="section-label">③ Required Variables</label>
          <div class="var-list">
            <div v-for="v in template.required_vars" :key="v.name" class="var-row">
              <label class="var-label">
                {{ v.name }}<span v-if="v.required" class="required">*</span>
                <span class="var-type">{{ v.description }}</span>
              </label>
              <input v-model="extraVars[v.name]" class="prv-input mono" :placeholder="String(v.default_value ?? '')" />
            </div>
          </div>
        </div>

        <!-- Risk warning for high-risk templates -->
        <div v-if="template.risk_level === 'high'" class="risk-warning">
          <span class="risk-icon">⚠</span>
          <div>
            <strong>High Risk</strong>
            <p>This template makes significant system changes. Double-check your target hosts.</p>
          </div>
        </div>

        <!-- Run summary -->
        <div class="run-summary">
          <div class="summary-row"><span class="lbl">Template</span><span class="val mono">{{ template.slug }}</span></div>
          <div class="summary-row"><span class="lbl">Target</span><span class="val mono">{{ inventory || '—' }}</span></div>
          <div class="summary-row"><span class="lbl">User</span><span class="val mono">{{ sshUser || '—' }}{{ sshBecome ? ' (sudo)' : '' }}</span></div>
          <div class="summary-row"><span class="lbl">Tasks</span><span class="val">{{ template.tasks.length }} tasks · ~{{ Math.round(template.estimated_duration_seconds / 60) }} min</span></div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-cancel" @click="$emit('close')">Cancel</button>
        <button class="btn-run" :disabled="running || !inventory.trim()" @click="run">
          <span v-if="running">◌ Starting…</span>
          <span v-else>▶ Run Now</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import { useEscapeKey } from '@/composables/useEscapeKey'
import type { Template } from '@/types'

const props = defineProps<{ template: Template }>()
const emit = defineEmits<{ close: [] }>()
useEscapeKey(() => emit('close'))

const router = useRouter()
const ui = useUiStore()

interface Asset { id: string; name: string; address: string; is_active: boolean; domain_name?: string }
interface JmsAccount { id: string; name: string; username: string; secret_type: string; privileged: boolean }
interface GwAccount { username: string; privileged: boolean }
interface Connectivity {
  has_gateway: boolean
  domain: { id: string; name: string } | null
  gateway: { host: string; port: string; accounts: GwAccount[] } | null
}

const inventory = ref('192.168.64.2')
const selectedAsset = ref<Asset | null>(null)
const sshUser = ref('test')
const sshPassword = ref('test')
const sshBecome = ref(true)
const gwUser = ref('')
const gwPassword = ref('')
const running = ref(false)
const extraVars = reactive<Record<string, string>>({})

const assets = ref<Asset[]>([])
const assetsLoading = ref(true)
const jmsAccounts = ref<JmsAccount[]>([])
const accountsLoading = ref(false)
const connectivity = ref<Connectivity | null>(null)
const connectivityLoading = ref(false)

onMounted(async () => {
  try {
    const resp = await api.get('/assets')
    assets.value = resp.data.items || []
  } catch {
    assets.value = []
  } finally {
    assetsLoading.value = false
  }
})

async function selectAsset(a: Asset) {
  selectedAsset.value = a
  inventory.value = a.address
  jmsAccounts.value = []
  connectivity.value = null
  gwUser.value = ''
  gwPassword.value = ''

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

async function run() {
  if (!inventory.value.trim()) return
  running.value = true
  try {
    const allVars: Record<string, unknown> = { ...extraVars }
    if (sshUser.value) allVars['ansible_user'] = sshUser.value
    if (sshPassword.value) {
      allVars['ansible_password'] = sshPassword.value
      allVars['ansible_become_password'] = sshPassword.value
    }
    if (sshBecome.value) allVars['ansible_become'] = true

    // Gateway vars (stripped by execution.py before building ansible extra-vars)
    if (connectivity.value?.has_gateway && connectivity.value.gateway && gwUser.value) {
      allVars['_gateway_host'] = connectivity.value.gateway.host
      allVars['_gateway_port'] = connectivity.value.gateway.port || '22'
      allVars['_gateway_user'] = gwUser.value
      if (gwPassword.value) allVars['_gateway_password'] = gwPassword.value
    }

    const resp = await api.post(`/templates/${props.template.slug}/run`, {
      inventory_selector: inventory.value.trim(),
      extra_vars: allVars,
    })
    ui.success('Job started — watching output')
    router.push(`/jobs/${resp.data.job_id}`)
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string }; status?: number } }
    ui.error(axErr?.response?.data?.detail || `Run failed (${axErr?.response?.status ?? 'network error'})`)
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.75);
  display: flex; align-items: center; justify-content: center; z-index: 600; padding: var(--space-4);
}
.modal {
  background: var(--bg-surface); border: 1px solid var(--border-muted);
  border-radius: var(--radius-xl); width: 100%; max-width: 620px; max-height: 92vh;
  display: flex; flex-direction: column; box-shadow: var(--shadow-lg); overflow: hidden;
}
.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); flex-shrink: 0;
}
.modal-title-row { display: flex; align-items: center; gap: var(--space-2); }
.run-icon { color: var(--success); font-size: 16px; }
.modal-title-row h3 { font-size: 15px; font-weight: 700; }
.template-name { font-size: 12px; color: var(--text-muted); background: var(--bg-subtle); padding: 2px 8px; border-radius: var(--radius-full); font-family: var(--font-mono); }
.close-btn { background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 4px; }
.close-btn:hover { color: var(--text); }

.modal-body { flex: 1; overflow-y: auto; padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-5); }

.section { display: flex; flex-direction: column; gap: var(--space-3); }
.section-label { font-size: 11px; font-weight: 700; color: var(--text); text-transform: uppercase; letter-spacing: 0.08em; }

.asset-grid { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.asset-btn {
  display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-overlay);
  cursor: pointer; font-size: 12px; color: var(--text-muted); transition: all var(--transition);
}
.asset-btn.active { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.asset-btn:hover:not(.active) { border-color: var(--border-muted); color: var(--text); }
.asset-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: var(--success); }
.dot-off { background: var(--text-subtle); }
.asset-ip { font-family: var(--font-mono); font-weight: 600; }
.asset-name { color: var(--text-subtle); font-size: 11px; }

.quick-targets { display: flex; gap: var(--space-2); }
.qt-btn { padding: var(--space-1) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-full); background: none; color: var(--text-muted); font-size: 12px; cursor: pointer; transition: all var(--transition); }
.qt-btn.active, .qt-btn:hover { border-color: var(--accent); color: var(--accent); }

.field-row { display: flex; flex-direction: column; gap: var(--space-1); }
.field-label { font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
.hint-text { font-size: 11px; color: var(--text-subtle); font-style: italic; }

.jms-accounts { display: flex; flex-direction: column; gap: var(--space-2); }
.account-list { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.account-btn {
  display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-overlay);
  cursor: pointer; font-size: 12px; color: var(--text-muted); transition: all var(--transition);
}
.account-btn.active { border-color: var(--success); color: var(--success); background: var(--success-dim); }
.account-btn:hover:not(.active) { border-color: var(--border-muted); color: var(--text); }
.acc-icon { font-size: 13px; }
.acc-user { font-family: var(--font-mono); font-weight: 600; }
.acc-type { font-size: 10px; color: var(--text-subtle); background: var(--bg-subtle); padding: 1px 5px; border-radius: var(--radius-sm); }

.creds-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.field { display: flex; flex-direction: column; gap: var(--space-1); }
.checkbox-row { display: flex; align-items: center; gap: var(--space-2); font-size: 12px; color: var(--text-muted); cursor: pointer; }
.checkbox-row code { font-family: var(--font-mono); background: var(--bg-subtle); padding: 1px 4px; border-radius: var(--radius-sm); }

.prv-input {
  background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md);
  color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3);
  outline: none; width: 100%; transition: border-color var(--transition);
}
.prv-input.mono { font-family: var(--font-mono); }
.prv-input:focus { border-color: var(--accent); }

.var-list { display: flex; flex-direction: column; gap: var(--space-3); }
.var-row { display: flex; flex-direction: column; gap: var(--space-1); }
.var-label { font-size: 12px; font-weight: 600; color: var(--text); font-family: var(--font-mono); display: flex; align-items: center; gap: var(--space-2); }
.var-type { font-size: 11px; color: var(--text-muted); font-family: var(--font-sans); font-weight: 400; }
.required { color: var(--error); }

.risk-warning { display: flex; gap: var(--space-3); background: var(--error-dim); border: 1px solid var(--error); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); font-size: 12px; }
.risk-icon { font-size: 18px; color: var(--error); }
.risk-warning strong { color: var(--error); display: block; margin-bottom: 4px; }
.risk-warning p { color: var(--text-muted); margin: 0; }

.run-summary { background: var(--bg-overlay); border: 1px solid var(--border); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); }
.summary-row { display: flex; align-items: center; gap: var(--space-3); font-size: 12px; }
.lbl { color: var(--text-muted); width: 70px; flex-shrink: 0; }
.val { color: var(--text); }
.mono { font-family: var(--font-mono); }

.modal-footer { display: flex; justify-content: flex-end; gap: var(--space-3); padding: var(--space-4) var(--space-5); border-top: 1px solid var(--border); flex-shrink: 0; }
.btn-cancel { background: none; border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-5); font-size: 13px; cursor: pointer; }
.btn-cancel:hover { color: var(--text); }
.btn-run { background: var(--success); color: #000; border: none; border-radius: var(--radius-md); padding: var(--space-2) var(--space-6); font-size: 13px; font-weight: 700; cursor: pointer; transition: all var(--transition); }
.btn-run:hover:not(:disabled) { background: #4cae5e; }
.btn-run:disabled { opacity: 0.5; cursor: not-allowed; }

/* Gateway section */
.gateway-section { background: var(--accent-dim); border: 1px solid var(--accent); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); margin-top: var(--space-2); }
.gw-header { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.gw-title { font-size: 12px; font-weight: 700; color: var(--accent); }
.gw-host { font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); }
.acc-quick-sm { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.creds-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.field { display: flex; flex-direction: column; gap: var(--space-1); }
</style>
