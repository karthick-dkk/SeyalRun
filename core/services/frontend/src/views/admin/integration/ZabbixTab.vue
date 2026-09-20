<template>
  <div>
    <div class="card">
      <div class="card-header">
        Zabbix connection
        <button class="btn btn-sm" @click="load">Refresh</button>
      </div>
      <div class="u-p-18">
        <div class="text-muted" v-if="loading">Loading…</div>
        <template v-else>
          <div class="int-grid">
            <div class="int-row">
              <span class="int-k">Zabbix URL</span>
              <a v-if="info.zabbix_url" :href="info.zabbix_url" target="_blank" class="int-link">{{ info.zabbix_url }}</a>
              <span class="text-muted" v-else>— not set</span>
            </div>
            <div class="int-row">
              <span class="int-k">API endpoint</span>
              <span class="ip-mono">{{ info.api_url || '—' }}</span>
            </div>
            <div class="int-row">
              <span class="int-k">Status</span>
              <span class="badge" :class="info.reachable ? 'badge-green' : (info.configured ? 'badge-red' : 'badge-gray')">
                {{ info.reachable ? 'Connected' : (info.configured ? 'Unreachable' : 'Not configured') }}
              </span>
              <span v-if="info.version" style="color:var(--text2);font-size:12px;margin-left:8px">Zabbix {{ info.version }}</span>
            </div>
          </div>

          <div style="display:flex;gap:10px;margin-top:18px">
            <a v-if="info.zabbix_url" class="btn btn-primary" :href="info.zabbix_url" target="_blank">↗ Open Zabbix</a>
          </div>

          <p style="margin-top:18px;color:var(--text2);font-size:12px;line-height:1.6">
            The Zabbix URL is stored in the database (superadmin-editable below) and falls back to
            <code>.env</code> (<code>ZABBIX_CONSOLE_URL</code> / <code>ZABBIX_API_URL</code>). The API token is
            kept server-side and never shown here.
          </p>
        </template>
      </div>
    </div>

    <div v-if="auth.isSuperAdmin" class="card u-mt-16">
      <div class="card-header">Zabbix Settings <span class="badge badge-blue">superadmin</span></div>
      <div class="u-p-18">
        <div class="fp-field">
          <label class="fp-label">Zabbix Console URL <span class="hint">(the "Open Zabbix" link / header button)</span></label>
          <input v-model="form.zabbix_console_url" class="fp-input" placeholder="https://zabbix.example.com" />
        </div>
        <div class="fp-field">
          <label class="fp-label">Zabbix API URL <span class="hint">(used for reachability + host sync)</span></label>
          <input v-model="form.zabbix_api_url" class="fp-input" placeholder="https://zabbix.example.com" />
        </div>
        <div class="fp-field">
          <label class="fp-label">
            Zabbix API Token
            <span class="hint">{{ settings.zabbix_api_token_configured ? '· currently configured — leave blank to keep' : '· not set' }}</span>
          </label>
          <input v-model="form.zabbix_api_token" type="password" autocomplete="new-password" class="fp-input" placeholder="leave blank to keep existing" />
        </div>
        <div v-if="saveMsg" class="save-msg" :class="saveErr ? 'err' : 'ok'">{{ saveMsg }}</div>
        <div class="mt-12">
          <button class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        </div>
      </div>
    </div>

    <PluginList
      class="u-mt-16"
      title="Zabbix plugins"
      description="Trigger sources parse an inbound Zabbix event into a job run, and the Zabbix SSO identity provider signs in users arriving from the Zabbix frontend module. Both ship inside the image; disabling one stops SeyalRun using it without removing anything."
      empty-text="No Zabbix trigger sources loaded. The Zabbix webhook path does not require one — see the Webhooks tab."
      :axes="['triggers', 'idp']"
      :only="zabbixPluginNames"
      :plugins="plugins"
      :can-toggle="auth.isSuperAdmin"
      @changed="$emit('changed')"
    />

    <div class="card u-mt-16">
      <div class="card-header">Open SeyalRun from the Zabbix UI</div>
      <div class="u-borderbottom-1solidborder_c-text2_fs-12_p-1016">
        Add these as <strong>global scripts</strong> in Zabbix (Alerts → Scripts). They appear in the
        host / problem context menus. The one-click webhook variant lives on the
        <strong>Webhooks</strong> tab.
      </div>
      <div style="padding:16px;display:flex;flex-direction:column;gap:18px">
        <div class="zs-block">
          <div class="zs-title">SSH Terminal from a host <span class="badge badge-blue">URL · Manual host action</span></div>
          <div class="zs-desc">Opens SeyalRun's terminal and auto-connects using the credential the operator is authorized for. Secured by SeyalRun login + RBAC.</div>
          <div class="zs-code"><code>{{ sshUrl }}</code><button class="btn-pill btn-pill-outline" @click="copy(sshUrl)">Copy</button></div>
        </div>

        <div class="zs-block">
          <div class="zs-title">Run remediation in the console <span class="badge badge-blue">URL · Manual event action</span></div>
          <div class="zs-desc">Opens SeyalRun pre-targeted to the problem's host + bound playbook. Operator confirms and runs; output posts back to the Problem.</div>
          <div class="zs-code"><code>{{ runUrl }}</code><button class="btn-pill btn-pill-outline" @click="copy(runUrl)">Copy</button></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import PluginList from '@/components/integration/PluginList.vue'

defineProps<{ plugins: any[] }>()
defineEmits<{ (e: 'changed'): void }>()

const auth = useAuthStore()

// The Zabbix tab shows the Zabbix IdP only — the full identity-provider list
// belongs to the provider that owns it (JumpServer's is on its own tab).
const zabbixPluginNames = ['zabbix_sso', 'zabbix_action_webhook', 'zabbix_trigger']

const info = ref<any>({})
const settings = ref<any>({})
const loading = ref(false)
const saving = ref(false)
const saveMsg = ref('')
const saveErr = ref(false)
const form = reactive({ zabbix_console_url: '', zabbix_api_url: '', zabbix_api_token: '' })

const origin = window.location.origin
const sshUrl = `${origin}/#/terminal?zbx_host={HOST.ID}&autoconnect=1`
const runUrl = `${origin}/#/zbx/run?zbx_host={HOST.ID}&event={EVENT.ID}&trigger={TRIGGER.ID}&severity={EVENT.NSEVERITY}`

async function load() {
  loading.value = true
  try { info.value = (await api.get('/integration/info')).data }
  catch { info.value = {} }
  finally { loading.value = false }
  if (auth.isSuperAdmin) {
    try {
      settings.value = (await api.get('/settings/integration')).data
      form.zabbix_console_url = settings.value.zabbix_console_url || ''
      form.zabbix_api_url = settings.value.zabbix_api_url || ''
      form.zabbix_api_token = ''
    } catch { settings.value = {} }
  }
}

async function save() {
  saving.value = true; saveMsg.value = ''; saveErr.value = false
  try {
    await api.put('/settings/integration', { ...form })
    form.zabbix_api_token = ''
    saveMsg.value = 'Saved.'
    await load()
  } catch (e: any) {
    saveErr.value = true
    saveMsg.value = e?.response?.data?.detail || 'Failed to save settings'
  } finally { saving.value = false }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
}

onMounted(load)
</script>

<style scoped>
.int-grid { display: flex; flex-direction: column; gap: 12px; }
.int-row { display: flex; align-items: center; gap: 12px; font-size: 14px; }
.int-k { flex: 0 0 130px; color: var(--text2); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.int-link { color: var(--accent2); }
code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.hint { color: var(--text2); font-weight: 400; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; }
.save-msg { font-size: 12px; margin-top: 10px; }
.save-msg.ok { color: var(--accent); }
.save-msg.err { color: var(--danger); }
.zs-block { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.zs-title { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 8px; }
.zs-desc { font-size: 12px; color: var(--text2); margin: 6px 0 10px; }
.zs-code { display: flex; align-items: flex-start; gap: 8px; background: var(--bg3); border-radius: 6px; padding: 10px 12px; }
.zs-code code, .zs-code pre { flex: 1; color: var(--text); font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.zs-code .btn-pill { flex-shrink: 0; }
</style>
