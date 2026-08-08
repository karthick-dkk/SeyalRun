<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title-row">
          <span class="ssh-icon">⊙</span>
          <h3>Open SSH Session</h3>
          <span class="host-chip">{{ asset.address }}</span>
        </div>
        <button class="close-btn" @click="$emit('close')">✕</button>
      </div>

      <div class="modal-body">
        <div class="section">
          <label class="section-label">SSH Credentials</label>

          <!-- JumpServer accounts -->
          <div v-if="accountsLoading" class="hint">Loading accounts from JumpServer…</div>
          <div v-else-if="jmsAccounts.length" class="acc-list">
            <button
              v-for="acc in jmsAccounts" :key="acc.username"
              class="acc-btn" :class="{ active: sshUser === acc.username }"
              @click="sshUser = acc.username"
            >
              {{ acc.privileged ? '⚡' : '👤' }} {{ acc.username }}
              <span class="acc-type">{{ acc.secret_type }}</span>
            </button>
          </div>

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
        </div>

        <!-- Gateway -->
        <template v-if="connectivity?.has_gateway">
          <div class="gateway-section">
            <div class="gw-header">
              <span>🔗</span>
              <span class="gw-title">Gateway — {{ connectivity.domain?.name }}</span>
              <span class="gw-host mono">{{ connectivity.gateway?.host }}:{{ connectivity.gateway?.port }}</span>
            </div>
            <div class="creds-grid">
              <div class="field">
                <label class="field-label">Gateway User</label>
                <input v-model="gwUser" class="prv-input mono" placeholder="gateway-user" />
              </div>
              <div class="field">
                <label class="field-label">Gateway Password</label>
                <input v-model="gwPassword" type="password" class="prv-input" placeholder="••••" />
              </div>
            </div>
          </div>
        </template>

        <div class="session-info">
          <span>Connection will open in a new terminal tab</span>
          <span class="mono text-muted">{{ sshUser }}@{{ asset.address }}</span>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn-cancel" @click="$emit('close')">Cancel</button>
        <button class="btn-connect" :disabled="connecting || !sshUser" @click="connect">
          <span v-if="connecting">⊙ Connecting…</span>
          <span v-else>⊙ Open Terminal</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { useUiStore } from '@/stores/ui'
import { useEscapeKey } from '@/composables/useEscapeKey'

interface Asset { id: string; name: string; address: string; domain_name?: string }
interface JmsAccount { username: string; privileged: boolean; secret_type: string }
interface Connectivity {
  has_gateway: boolean
  domain: { id: string; name: string } | null
  gateway: { host: string; port: string; accounts: JmsAccount[] } | null
}

const props = defineProps<{ asset: Asset }>()
const emit = defineEmits<{ close: [] }>()
useEscapeKey(() => emit('close'))

const router = useRouter()
const ui = useUiStore()

const sshUser = ref('test')
const sshPassword = ref('test')
const gwUser = ref('')
const gwPassword = ref('')
const connecting = ref(false)
const jmsAccounts = ref<JmsAccount[]>([])
const accountsLoading = ref(true)
const connectivity = ref<Connectivity | null>(null)

onMounted(async () => {
  const [accResp, connResp] = await Promise.allSettled([
    api.get(`/assets/${props.asset.id}/accounts`),
    api.get(`/assets/${props.asset.id}/connectivity`),
  ])
  if (accResp.status === 'fulfilled') {
    jmsAccounts.value = accResp.value.data.accounts || []
    const priv = jmsAccounts.value.find(a => a.privileged) || jmsAccounts.value[0]
    if (priv) sshUser.value = priv.username
  }
  accountsLoading.value = false

  if (connResp.status === 'fulfilled') {
    connectivity.value = connResp.value.data
    if (connectivity.value?.has_gateway && connectivity.value.gateway?.accounts?.length) {
      const gwa = connectivity.value.gateway.accounts.find(a => a.privileged) || connectivity.value.gateway.accounts[0]
      if (gwa) gwUser.value = gwa.username
    }
  }
})

async function connect() {
  if (!sshUser.value) return
  connecting.value = true
  try {
    const gateway = (connectivity.value?.has_gateway && gwUser.value)
      ? {
          host: connectivity.value.gateway?.host,
          port: connectivity.value.gateway?.port || '22',
          user: gwUser.value,
          password: gwPassword.value,
        }
      : null

    const resp = await api.post('/ssh/sessions', {
      asset_id: props.asset.id,
      asset_name: props.asset.name,
      asset_address: props.asset.address,
      ssh_username: sshUser.value,
      ssh_password: sshPassword.value,
      gateway,
    })

    const sessionId = resp.data.session_id
    router.push({ name: 'SSHTerminal', query: { session: sessionId } })
  } catch (e: unknown) {
    const axErr = e as { response?: { data?: { detail?: string } } }
    ui.error(axErr?.response?.data?.detail || 'Failed to create session')
  } finally {
    connecting.value = false
  }
}
</script>

<style scoped>
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.75); display: flex; align-items: center; justify-content: center; z-index: 650; }
.modal { background: var(--bg-surface); border: 1px solid var(--border-muted); border-radius: var(--radius-xl); width: 480px; max-height: 85vh; display: flex; flex-direction: column; box-shadow: var(--shadow-lg); overflow: hidden; }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border); flex-shrink: 0; }
.modal-title-row { display: flex; align-items: center; gap: var(--space-2); }
.ssh-icon { color: var(--success); font-size: 16px; }
.modal-title-row h3 { font-size: 15px; font-weight: 700; }
.host-chip { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); background: var(--bg-subtle); padding: 2px 8px; border-radius: var(--radius-full); }
.close-btn { background: none; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; }
.modal-body { flex: 1; overflow-y: auto; padding: var(--space-5); display: flex; flex-direction: column; gap: var(--space-4); }
.section { display: flex; flex-direction: column; gap: var(--space-3); }
.section-label { font-size: 10px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.08em; }
.hint { font-size: 11px; color: var(--text-subtle); }
.acc-list { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.acc-btn { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-overlay); color: var(--text-muted); font-size: 12px; cursor: pointer; transition: all var(--transition); }
.acc-btn.active { border-color: var(--success); color: var(--success); background: var(--success-dim); }
.acc-type { font-size: 10px; color: var(--text-subtle); background: var(--bg-subtle); padding: 1px 5px; border-radius: var(--radius-sm); }
.creds-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.field { display: flex; flex-direction: column; gap: var(--space-1); }
.field-label { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; }
.prv-input { background: var(--bg-base); border: 1px solid var(--border); border-radius: var(--radius-md); color: var(--text); font-size: 13px; padding: var(--space-2) var(--space-3); outline: none; width: 100%; }
.prv-input.mono { font-family: var(--font-mono); }
.prv-input:focus { border-color: var(--accent); }
.gateway-section { background: var(--accent-dim); border: 1px solid var(--accent); border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); }
.gw-header { display: flex; align-items: center; gap: var(--space-2); }
.gw-title { font-size: 12px; font-weight: 700; color: var(--accent); }
.gw-host { font-size: 11px; color: var(--text-muted); }
.session-info { display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: var(--text-muted); padding: var(--space-2) var(--space-3); background: var(--bg-overlay); border-radius: var(--radius-md); }
.mono { font-family: var(--font-mono); }
.text-muted { color: var(--text-muted); }
.modal-footer { display: flex; justify-content: flex-end; gap: var(--space-3); padding: var(--space-4) var(--space-5); border-top: 1px solid var(--border); flex-shrink: 0; }
.btn-cancel { background: none; border: 1px solid var(--border); color: var(--text-muted); border-radius: var(--radius-md); padding: var(--space-2) var(--space-5); font-size: 13px; cursor: pointer; }
.btn-connect { background: var(--success); color: #000; border: none; border-radius: var(--radius-md); padding: var(--space-2) var(--space-5); font-size: 13px; font-weight: 700; cursor: pointer; transition: all var(--transition); }
.btn-connect:hover:not(:disabled) { background: #4cae5e; }
.btn-connect:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
