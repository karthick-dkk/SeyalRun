<template>
  <div>
    <PluginList
      title="Automation executors"
      description="Each executor runs one action_type. Disabling one is enforced at dispatch: an ad-hoc run, a schedule and a Zabbix trigger are all refused and recorded as a failed run, rather than the switch only hiding it in the UI."
      :axes="['executors']"
      :plugins="plugins"
      :can-toggle="auth.isSuperAdmin"
      @changed="$emit('changed')"
    />

    <!-- ── Salt master ──────────────────────────────────────────────────── -->
    <div class="card u-mt-16">
      <div class="card-header">
        Salt master
        <span class="badge" :class="saltReady ? 'badge-green' : 'badge-gray'">{{ saltReady ? 'Configured' : 'Not configured' }}</span>
      </div>
      <div class="u-p-18">
        <p class="note u-mt-0">
          Runs a Salt function on minions through <code>salt-api</code>. The Salt master already owns the
          trust relationship with its minions, so this path uses no SeyalRun credential and opens no SSH
          connection — targets are minion ids, not SeyalRun hosts. Create a job template with
          <code>action_type = salt_master</code> and params <code>fun</code>, <code>tgt</code>
          (and optionally <code>tgt_type</code>, <code>arg</code>, <code>kwarg</code>).
        </p>

        <template v-if="auth.isSuperAdmin">
          <div class="fp-field">
            <label class="fp-label">salt-api URL</label>
            <input v-model="salt.url" class="fp-input" placeholder="https://salt.example.com:8000" />
          </div>
          <div class="fp-row">
            <div class="fp-field flex-1">
              <label class="fp-label">eauth backend</label>
              <input v-model="salt.eauth" class="fp-input" placeholder="pam" />
            </div>
            <div class="fp-field flex-1">
              <label class="fp-label">Username</label>
              <input v-model="salt.eauth_username" class="fp-input" placeholder="saltapi" />
            </div>
          </div>
          <div class="fp-field">
            <label class="fp-label">
              Password
              <span class="hint">{{ cfg.salt?.eauth_password_configured ? '· currently configured — leave blank to keep' : '· not set' }}</span>
            </label>
            <input v-model="salt.eauth_password" type="password" autocomplete="new-password" class="fp-input" placeholder="leave blank to keep existing" />
          </div>
          <div class="fp-field">
            <label class="fp-label">CA bundle path <span class="hint">(optional — trust root for a self-signed salt-api; verification is never disabled)</span></label>
            <input v-model="salt.ca_bundle" class="fp-input" placeholder="/etc/seyalrun/tls/salt-ca.pem" />
          </div>
          <div v-if="saltMsg" class="save-msg" :class="saltErr ? 'err' : 'ok'">{{ saltMsg }}</div>
          <div class="mt-12">
            <button class="btn btn-primary" :disabled="savingSalt" @click="saveSalt">{{ savingSalt ? 'Saving…' : 'Save' }}</button>
          </div>
        </template>
        <p v-else class="note">Superadmin only.</p>
      </div>
    </div>

    <!-- ── Ansible Galaxy ───────────────────────────────────────────────── -->
    <div class="card u-mt-16">
      <div class="card-header">
        Ansible Galaxy
        <span class="badge" :class="cfg.galaxy?.server ? 'badge-green' : 'badge-gray'">{{ cfg.galaxy?.server ? 'Configured' : 'Default (galaxy.ansible.com)' }}</span>
      </div>
      <div class="u-p-18">
        <p class="note u-mt-0">
          Where the Ansible executor resolves collections and roles from. Point it at a private Galaxy /
          Automation Hub to keep playbook dependencies inside your own network.
        </p>
        <template v-if="auth.isSuperAdmin">
          <div class="fp-field">
            <label class="fp-label">Galaxy server URL</label>
            <input v-model="galaxy.server" class="fp-input" placeholder="https://galaxy.ansible.com" />
          </div>
          <div class="fp-field">
            <label class="fp-label">
              API key
              <span class="hint">{{ cfg.galaxy?.api_key_configured ? '· currently configured — leave blank to keep' : '· not set' }}</span>
            </label>
            <input v-model="galaxy.api_key" type="password" autocomplete="new-password" class="fp-input" placeholder="leave blank to keep existing" />
          </div>
          <div class="fp-field">
            <label class="fp-label">Collections <span class="hint">(one per line, e.g. community.general:>=8.0.0)</span></label>
            <textarea v-model="galaxy.collections" class="fp-input" rows="4" placeholder="community.general&#10;ansible.posix"></textarea>
          </div>
          <div v-if="galaxyMsg" class="save-msg" :class="galaxyErr ? 'err' : 'ok'">{{ galaxyMsg }}</div>
          <div class="mt-12">
            <button class="btn btn-primary" :disabled="savingGalaxy" @click="saveGalaxy">{{ savingGalaxy ? 'Saving…' : 'Save' }}</button>
          </div>
          <p class="note">
            Saving records the configuration. Collections are <strong>not</strong> fetched into a running
            container — that would change what a deployed image does at runtime, which is exactly what the
            plugin model avoids. They are installed when the automation image is built.
          </p>
        </template>
        <p v-else class="note">Superadmin only.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import PluginList from '@/components/integration/PluginList.vue'

defineProps<{ plugins: any[] }>()
defineEmits<{ (e: 'changed'): void }>()

const auth = useAuthStore()

const cfg = ref<any>({})
const salt = reactive({ url: '', eauth: 'pam', eauth_username: '', eauth_password: '', ca_bundle: '' })
const galaxy = reactive({ server: '', api_key: '', collections: '' })

const savingSalt = ref(false); const saltMsg = ref(''); const saltErr = ref(false)
const savingGalaxy = ref(false); const galaxyMsg = ref(''); const galaxyErr = ref(false)

// "Ready" means the executor has everything it needs to authenticate. A URL alone
// is not configured — it would fail at run time with a 401 nobody expected.
const saltReady = computed(() => !!(cfg.value.salt?.url && cfg.value.salt?.eauth_username && cfg.value.salt?.eauth_password_configured))

async function loadState() {
  try {
    const { data } = await api.get('/integrations/state')
    cfg.value = data.config || {}
    salt.url = cfg.value.salt?.url || ''
    salt.eauth = cfg.value.salt?.eauth || 'pam'
    salt.eauth_username = cfg.value.salt?.eauth_username || ''
    salt.eauth_password = ''
    salt.ca_bundle = cfg.value.salt?.ca_bundle || ''
    galaxy.server = cfg.value.galaxy?.server || ''
    galaxy.api_key = ''
    galaxy.collections = cfg.value.galaxy?.collections || ''
  } catch { cfg.value = {} }
}

async function saveSalt() {
  savingSalt.value = true; saltMsg.value = ''; saltErr.value = false
  try {
    await api.put('/integrations/config/salt', { values: { ...salt } })
    salt.eauth_password = ''
    saltMsg.value = 'Saved.'
    await loadState()
  } catch (e: any) {
    saltErr.value = true
    saltMsg.value = e?.response?.data?.detail || 'Failed to save'
  } finally { savingSalt.value = false }
}

async function saveGalaxy() {
  savingGalaxy.value = true; galaxyMsg.value = ''; galaxyErr.value = false
  try {
    await api.put('/integrations/config/galaxy', { values: { ...galaxy } })
    galaxy.api_key = ''
    galaxyMsg.value = 'Saved.'
    await loadState()
  } catch (e: any) {
    galaxyErr.value = true
    galaxyMsg.value = e?.response?.data?.detail || 'Failed to save'
  } finally { savingGalaxy.value = false }
}

onMounted(loadState)
</script>

<style scoped>
.note { color: var(--text2); font-size: 12px; line-height: 1.6; margin-top: 16px; }
.u-mt-0 { margin-top: 0; margin-bottom: 16px; }
code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.fp-row { display: flex; gap: 12px; }
.flex-1 { flex: 1; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.hint { color: var(--text2); font-weight: 400; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; font-family: inherit; }
.save-msg { font-size: 12px; margin-top: 10px; }
.save-msg.ok { color: var(--accent); }
.save-msg.err { color: var(--danger); }
</style>
