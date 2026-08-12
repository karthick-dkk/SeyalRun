<template>
  <div>
    <div class="card">
      <div class="card-header">
        Zabbix Integration
        <button class="btn btn-sm" @click="load">Refresh</button>
      </div>
      <div class="u-p-18">
        <p class="lede">How SeyalRun finds and talks to your Zabbix — the same URL used by the header's "Zabbix" link, host sync, and the webhook result post-back.</p>
        <div class="text-muted" v-if="loading">Loading…</div>
        <template v-else>
          <div class="fp-field">
            <label class="fp-label">Zabbix Console URL <span class="hint">(the "Back to Zabbix" link)</span></label>
            <input v-model="integrationForm.zabbix_console_url" class="fp-input" placeholder="https://zabbix.example.com" />
          </div>
          <div class="fp-field">
            <label class="fp-label">Zabbix API URL <span class="hint">(used for reachability + host sync)</span></label>
            <input v-model="integrationForm.zabbix_api_url" class="fp-input" placeholder="https://zabbix.example.com" />
          </div>
          <div class="fp-field">
            <label class="fp-label">
              Zabbix API Token
              <span class="hint">{{ integrationConfigured ? '· currently configured — leave blank to keep' : '· not set' }}</span>
            </label>
            <input v-model="integrationForm.zabbix_api_token" type="password" autocomplete="new-password" class="fp-input" placeholder="leave blank to keep existing" />
          </div>
          <div v-if="integrationMsg" class="save-msg" :class="integrationErr ? 'err' : 'ok'">{{ integrationMsg }}</div>
          <div class="mt-12">
            <button class="btn btn-primary" :disabled="integrationSaving" @click="saveIntegration">{{ integrationSaving ? 'Saving…' : 'Save' }}</button>
          </div>
        </template>
      </div>
    </div>

    <div class="card u-mt-16">
      <div class="card-header">
        Traffic &amp; Session Limits
        <button class="btn btn-sm" @click="load">Refresh</button>
      </div>
      <div class="u-p-18">
        <p class="lede">Changes apply immediately across every SeyalRun user — no restart, no redeploy.</p>
        <div class="text-muted" v-if="loading">Loading…</div>
        <template v-else>
          <div class="fp-grid">
            <div class="fp-field">
              <label class="fp-label">Requests allowed per user <span class="hint">(per window, below)</span></label>
              <input v-model.number="platformForm.rate_limit_requests" type="number" min="10" class="fp-input" />
            </div>
            <div class="fp-field">
              <label class="fp-label">Window (seconds)</label>
              <input v-model.number="platformForm.rate_limit_window_seconds" type="number" min="1" class="fp-input" />
            </div>
            <div class="fp-field">
              <label class="fp-label">Sign out after idle (minutes)</label>
              <input v-model.number="platformForm.session_idle_minutes" type="number" min="1" class="fp-input" />
            </div>
            <div class="fp-field">
              <label class="fp-label">Sign out at latest (hours) <span class="hint">absolute cap, active or not</span></label>
              <input v-model.number="platformForm.session_absolute_hours" type="number" min="1" class="fp-input" />
            </div>
          </div>
          <div class="fp-field" style="margin-top:4px">
            <label class="fp-label">App Public URL <span class="hint">used to build one-click links in approval emails</span></label>
            <input v-model="platformForm.app_public_url" class="fp-input" placeholder="https://seyalrun.example.com" />
          </div>
          <div class="fp-field" style="margin-top:4px">
            <label class="fp-label">Skip approval for <span class="hint">these roles' own access-grant requests go active immediately, no second approver needed</span></label>
            <div style="display:flex;gap:16px;margin-top:2px">
              <label class="toggle-row"><input type="checkbox" v-model="exemptRoleAdmin" /> Admin</label>
              <label class="toggle-row"><input type="checkbox" v-model="exemptRoleSuperadmin" /> Superadmin</label>
            </div>
          </div>
          <div v-if="platformMsg" class="save-msg" :class="platformErr ? 'err' : 'ok'">{{ platformMsg }}</div>
          <div class="mt-12">
            <button class="btn btn-primary" :disabled="platformSaving" @click="savePlatform">{{ platformSaving ? 'Saving…' : 'Save' }}</button>
          </div>
        </template>
      </div>
    </div>

    <div class="card u-mt-16">
      <div class="card-header">Zabbix Module <span class="badge badge-blue">superadmin</span></div>
      <div class="u-p-18">
        <p class="lede">
          Controls the SeyalRun menu &amp; SSH-Hosts page embedded inside Zabbix. The module's own
          identity assertion (<code>ZABBIX_MODULE_SECRET</code>) always stays in <code>.env</code> — this
          only turns the integration on/off and grants a higher request allowance to the Zabbix
          server's own signed calls, not to end users browsing the embedded pages.
        </p>
        <div class="text-muted" v-if="loading">Loading…</div>
        <template v-else>
          <label class="toggle-row">
            <input type="checkbox" v-model="moduleForm.enabled" />
            Show the SeyalRun menu inside Zabbix
          </label>
          <div class="fp-field mt-12">
            <label class="fp-label">Requests allowed for the Zabbix server itself</label>
            <input v-model.number="moduleForm.elevated_rate_limit" type="number" min="10" class="fp-input" />
          </div>
          <div class="fp-field">
            <label class="fp-label">Which Zabbix server IPs to trust <span class="hint">comma-separated, optional</span></label>
            <input v-model="moduleTrustedIpsText" class="fp-input" placeholder="10.20.0.5, 10.20.0.6" />
          </div>
          <div v-if="moduleMsg" class="save-msg" :class="moduleErr ? 'err' : 'ok'">{{ moduleMsg }}</div>
          <div class="mt-12">
            <button class="btn btn-primary" :disabled="moduleSaving" @click="saveModule">{{ moduleSaving ? 'Saving…' : 'Save' }}</button>
          </div>
        </template>
      </div>
    </div>

    <div class="card u-mt-16">
      <div class="card-header">Not editable here, on purpose</div>
      <div class="u-p-18">
        <p style="color:var(--text2);font-size:12px;line-height:1.6">
          A few values are too risky to change from a settings page — get them wrong and every stored
          password/key silently stops decrypting. Those stay in the server's <code>.env</code> file,
          changed only by whoever manages the deployment:
          <code>ZA_VAULT_PASSWORD</code>, <code>ZA_VAULT_SALT</code>, <code>JWT_SECRET</code>,
          <code>SERVICE_JWT_SECRET</code>, <code>API_TOKEN_PEPPER</code>, <code>ZABBIX_MODULE_SECRET</code>,
          <code>ZABBIX_WEBHOOK_HMAC_SECRET</code>, <code>DB_*</code>.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import api from '@/api/client'

const loading = ref(false)

const integrationForm = reactive({ zabbix_console_url: '', zabbix_api_url: '', zabbix_api_token: '' })
const integrationConfigured = ref(false)
const integrationSaving = ref(false)
const integrationMsg = ref('')
const integrationErr = ref(false)

const platformForm = reactive({
  rate_limit_requests: 600, rate_limit_window_seconds: 60,
  session_idle_minutes: 30, session_absolute_hours: 8, log_level: 'INFO',
  app_public_url: '', authorization_approval_exempt_roles: ['superadmin'] as string[],
})
const platformSaving = ref(false)
const platformMsg = ref('')
const platformErr = ref(false)

// Bridges the backend's role-name array to two checkboxes — only "admin" and
// "superadmin" are meaningful here (create/update authorization is
// admin-gated, so no other role can ever reach it). Top-level computed refs
// (not nested in an object) so Vue's template ref-unwrapping applies to
// v-model, matching moduleTrustedIpsText's pattern above.
function exemptRoleToggle(role: string) {
  return computed({
    get: () => platformForm.authorization_approval_exempt_roles.includes(role),
    set: (checked: boolean) => {
      const roles = platformForm.authorization_approval_exempt_roles
      if (checked && !roles.includes(role)) roles.push(role)
      else if (!checked) platformForm.authorization_approval_exempt_roles = roles.filter(r => r !== role)
    },
  })
}
const exemptRoleAdmin = exemptRoleToggle('admin')
const exemptRoleSuperadmin = exemptRoleToggle('superadmin')

const moduleForm = reactive({ enabled: false, trusted_ips: [] as string[], elevated_rate_limit: 5000 })
const moduleTrustedIpsText = computed({
  get: () => moduleForm.trusted_ips.join(', '),
  set: (v: string) => { moduleForm.trusted_ips = v.split(',').map(s => s.trim()).filter(Boolean) },
})
const moduleSaving = ref(false)
const moduleMsg = ref('')
const moduleErr = ref(false)

async function load() {
  loading.value = true
  try {
    const [integration, platform, mod] = await Promise.all([
      api.get('/settings/integration'),
      api.get('/settings/platform'),
      api.get('/settings/zabbix-module'),
    ])
    integrationForm.zabbix_console_url = integration.data.zabbix_console_url || ''
    integrationForm.zabbix_api_url = integration.data.zabbix_api_url || ''
    integrationForm.zabbix_api_token = ''
    integrationConfigured.value = !!integration.data.zabbix_api_token_configured
    Object.assign(platformForm, platform.data)
    Object.assign(moduleForm, mod.data)
  } catch { /* keep defaults */ }
  finally { loading.value = false }
}

async function saveIntegration() {
  integrationSaving.value = true; integrationMsg.value = ''; integrationErr.value = false
  try {
    await api.put('/settings/integration', { ...integrationForm })
    integrationForm.zabbix_api_token = ''
    integrationMsg.value = 'Saved.'
    await load()
  } catch (e: any) {
    integrationErr.value = true
    integrationMsg.value = e?.response?.data?.detail || 'Failed to save settings'
  } finally { integrationSaving.value = false }
}

async function savePlatform() {
  platformSaving.value = true; platformMsg.value = ''; platformErr.value = false
  try {
    await api.put('/settings/platform', { ...platformForm })
    platformMsg.value = 'Saved.'
    await load()
  } catch (e: any) {
    platformErr.value = true
    platformMsg.value = e?.response?.data?.detail || 'Failed to save settings'
  } finally { platformSaving.value = false }
}

async function saveModule() {
  moduleSaving.value = true; moduleMsg.value = ''; moduleErr.value = false
  try {
    await api.put('/settings/zabbix-module', { ...moduleForm })
    moduleMsg.value = 'Saved.'
    await load()
  } catch (e: any) {
    moduleErr.value = true
    moduleMsg.value = e?.response?.data?.detail || 'Failed to save settings'
  } finally { moduleSaving.value = false }
}

onMounted(load)
</script>

<style scoped>
.lede { color: var(--text2); font-size: 12px; line-height: 1.6; margin: 0 0 16px; }
.fp-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.fp-field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.fp-label { font-size: 12px; color: var(--text2); font-weight: 500; }
.hint { color: var(--text2); font-weight: 400; }
.fp-input { padding: 7px 10px; background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; color: var(--text); font-size: 13px; outline: none; }
.toggle-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); cursor: pointer; }
.save-msg { font-size: 12px; margin-top: 10px; }
.save-msg.ok { color: var(--accent); }
.save-msg.err { color: var(--danger); }
code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
