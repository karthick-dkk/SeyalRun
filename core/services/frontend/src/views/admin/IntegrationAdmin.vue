<template>
  <div>
    <div class="card">
      <div class="card-header">
        Integration
        <button class="btn btn-sm" @click="load">Refresh</button>
      </div>
      <div style="padding:18px">
        <div v-if="loading" style="color:var(--text2)">Loading…</div>
        <template v-else>
          <div class="int-grid">
            <div class="int-row">
              <span class="int-k">Zabbix URL</span>
              <a v-if="info.zabbix_url" :href="info.zabbix_url" target="_blank" class="int-link">{{ info.zabbix_url }}</a>
              <span v-else style="color:var(--text2)">— not set</span>
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
            kept server-side and never shown here. (The webhook post-back still uses the service's own
            <code>.env</code> token.)
          </p>
        </template>
      </div>
    </div>

    <!-- Superadmin-only: edit the Zabbix integration settings -->
    <div v-if="auth.isSuperAdmin" class="card" style="margin-top:16px">
      <div class="card-header">Zabbix Settings <span class="badge badge-blue">superadmin</span></div>
      <div style="padding:18px">
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
        <div style="margin-top:12px">
          <button class="btn btn-primary" :disabled="saving" @click="save">{{ saving ? 'Saving…' : 'Save' }}</button>
        </div>
      </div>
    </div>

    <!-- ── Zabbix UI wiring (copy-paste global scripts) ──────────────────── -->
    <div class="card" style="margin-top:16px">
      <div class="card-header">Connect from the Zabbix UI</div>
      <div style="padding:10px 16px;font-size:12px;color:var(--text2);border-bottom:1px solid var(--border)">
        Add these as <strong>global scripts</strong> in Zabbix (Alerts → Scripts, or Administration → Scripts).
        They appear in the host / problem context menus. Set a Zabbix macro
        <code>{$SEYALRUN_HMAC}</code> = your webhook HMAC secret for the one-click option.
      </div>
      <div style="padding:16px;display:flex;flex-direction:column;gap:18px">

        <div class="zs-block">
          <div class="zs-title">1 · SSH Terminal from a host <span class="badge badge-blue">URL · Manual host action</span></div>
          <div class="zs-desc">Opens SeyalRun's terminal and auto-connects using the credential the operator is authorized for. Secured by SeyalRun login + RBAC.</div>
          <div class="zs-code"><code>{{ sshUrl }}</code><button class="btn-pill btn-pill-outline" @click="copy(sshUrl)">Copy</button></div>
        </div>

        <div class="zs-block">
          <div class="zs-title">2 · Run remediation in the console <span class="badge badge-blue">URL · Manual event action</span></div>
          <div class="zs-desc">Opens SeyalRun pre-targeted to the problem's host + bound playbook. Operator confirms and runs; output posts back to the Problem.</div>
          <div class="zs-code"><code>{{ runUrl }}</code><button class="btn-pill btn-pill-outline" @click="copy(runUrl)">Copy</button></div>
        </div>

        <div class="zs-block">
          <div class="zs-title">3 · One-click remediation <span class="badge badge-green">Webhook · Manual event action</span></div>
          <div class="zs-desc">Runs the pre-bound playbook server-side (HMAC-signed) without leaving Zabbix; the complete output is written onto the Problem. Requires Zabbix 6.4+ (<code>hmac()</code>).</div>
          <div style="font-size:12px;color:var(--text2);margin:4px 0">Parameters:</div>
          <div class="zs-code"><code>{{ webhookParams }}</code><button class="btn-pill btn-pill-outline" @click="copy(webhookParams)">Copy</button></div>
          <div style="font-size:12px;color:var(--text2);margin:8px 0 4px">Script:</div>
          <div class="zs-code"><pre style="margin:0">{{ webhookScript }}</pre><button class="btn-pill btn-pill-outline" @click="copy(webhookScript)">Copy</button></div>
        </div>

        <div class="zs-block" style="border-color:#1f6feb55">
          <div class="zs-title">Let the operator PICK the playbook</div>
          <div class="zs-desc">
            Add one more webhook parameter <code>binding</code>. The chosen playbook name is shown on the Problem
            (<code>playbook: …</code>), so you always see what ran. Two ways:
          </div>
          <div style="font-size:12px;color:var(--text);margin:4px 0"><strong>A · One script per playbook</strong> — set <code>binding</code> to a fixed name below. Name each Zabbix script after the playbook; the operator picks from the menu.</div>
          <div style="font-size:12px;color:var(--text);margin:8px 0 4px"><strong>B · One script, dropdown</strong> — set parameter <code>binding</code> = <code>&#123;MANUALINPUT&#125;</code>, then in the script's <em>Advanced configuration</em> enable <em>User input → Dropdown</em> and paste these options:</div>
          <div class="zs-code"><code>{{ bindingNames || '(create a binding first)' }}</code><button class="btn-pill btn-pill-outline" @click="copy(bindingNames)">Copy</button></div>
          <div class="zs-desc" style="margin-top:8px">
            Only enabled bindings are runnable — an unknown name is rejected (<code>no_binding</code>).
            Manage bindings on the <router-link to="/admin/trigger-bindings" style="color:var(--accent2)">Trigger Bindings</router-link> page.
          </div>
        </div>

      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// ── Zabbix URL / API token settings ─────────────────────────────────────────
const info = ref<any>({})
const settings = ref<any>({})
const loading = ref(false)
const saving = ref(false)
const saveMsg = ref('')
const saveErr = ref(false)
const form = reactive({ zabbix_console_url: '', zabbix_api_url: '', zabbix_api_token: '' })

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

// Binding names only — enough for the dropdown-picker snippet below. Full
// CRUD (create/edit/delete/run) lives on its own page: TriggerBindingsAdmin.vue.
const bindings = ref<any[]>([])

// Zabbix-side setup snippets (computed from this deployment's origin).
const origin = window.location.origin
const sshUrl = `${origin}/#/terminal?zbx_host={HOST.ID}&autoconnect=1`
const runUrl = `${origin}/#/zbx/run?zbx_host={HOST.ID}&event={EVENT.ID}&trigger={TRIGGER.ID}&severity={EVENT.NSEVERITY}`
const webhookParams = [
  `url         ${origin}/webhook/zabbix/execute`,
  `eventid     {EVENT.ID}`,
  `triggerid   {TRIGGER.ID}`,
  `hostname    {HOST.HOST}`,
  `severity    {EVENT.NSEVERITY}`,
  `hmac_secret {$SEYALRUN_HMAC}`,
].join('\n')
const webhookScript = `var p = JSON.parse(value);
var body = JSON.stringify({
  eventid: p.eventid, triggerid: p.triggerid,
  hostname: p.hostname, severity: parseInt(p.severity || '0'),
  host_groups: [], binding: p.binding || ''
});
var sig = hmac('sha256', p.hmac_secret, body);
var req = new HttpRequest();
req.addHeader('Content-Type: application/json');
req.addHeader('X-SeyalRun-Signature: ' + sig);
var resp = req.post(p.url, body);
if (req.getStatus() >= 400) throw 'SeyalRun ' + req.getStatus() + ': ' + resp;
return resp;`

// Comma-separated binding names for a Zabbix manual-input Dropdown.
const bindingNames = computed(() => bindings.value.map((b: any) => b.name).join(','))

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
}

async function loadBindingNames() {
  try { bindings.value = await api.get('/trigger-bindings').then(r => r.data) }
  catch { bindings.value = [] }
}

onMounted(() => { load(); loadBindingNames() })
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
