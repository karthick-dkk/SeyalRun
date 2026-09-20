<template>
  <div>
    <div class="card">
      <div class="card-header">Inbound webhook</div>
      <div class="u-p-18">
        <div class="int-grid">
          <div class="int-row">
            <span class="int-k">Endpoint</span>
            <span class="ip-mono">{{ webhookUrl }}</span>
            <button class="btn-pill btn-pill-outline" @click="copy(webhookUrl)">Copy</button>
          </div>
          <div class="int-row">
            <span class="int-k">Authentication</span>
            <span class="badge badge-green">HMAC-SHA256</span>
            <span style="color:var(--text2);font-size:12px">header <code>X-SeyalRun-Signature</code> over the raw body</span>
          </div>
          <div class="int-row">
            <span class="int-k">Bindings</span>
            <span style="font-size:13px">{{ bindings.length }} configured</span>
            <router-link to="/admin/trigger-bindings" class="int-link" style="font-size:12px">Manage →</router-link>
          </div>
        </div>
        <p class="note">
          This endpoint is reached <strong>directly</strong>, bypassing the API gateway — Zabbix calls it, so
          it has no session to present. Its only authentication is the shared HMAC secret
          (<code>ZABBIX_WEBHOOK_HMAC_SECRET</code>), and the service refuses to start if that secret is
          unset, so there is no unauthenticated deployment of this path. Only an <em>enabled binding</em>
          is runnable: an unknown binding name is rejected rather than falling back to a default.
        </p>
      </div>
    </div>

    <div class="card u-mt-16">
      <div class="card-header">One-click remediation from a Zabbix problem</div>
      <div class="u-borderbottom-1solidborder_c-text2_fs-12_p-1016">
        Add as a <strong>Webhook · Manual event action</strong> in Zabbix. Runs the pre-bound playbook
        server-side and writes the complete output onto the Problem, without leaving Zabbix. Requires
        Zabbix 6.4+ (for <code>hmac()</code>). Set the Zabbix macro <code>{$SEYALRUN_HMAC}</code> to your
        webhook HMAC secret.
      </div>
      <div style="padding:16px;display:flex;flex-direction:column;gap:18px">
        <div class="zs-block">
          <div class="zs-title">Parameters</div>
          <div class="zs-code"><code>{{ webhookParams }}</code><button class="btn-pill btn-pill-outline" @click="copy(webhookParams)">Copy</button></div>
          <div class="zs-title u-mt-8">Script</div>
          <div class="zs-code"><pre class="u-m-0">{{ webhookScript }}</pre><button class="btn-pill btn-pill-outline" @click="copy(webhookScript)">Copy</button></div>
        </div>

        <div class="zs-block" style="border-color:#1f6feb55">
          <div class="zs-title">Let the operator PICK the playbook</div>
          <div class="zs-desc">
            Add one more webhook parameter <code>binding</code>. The chosen playbook name is shown on the
            Problem (<code>playbook: …</code>), so you always see what ran. Two ways:
          </div>
          <div style="font-size:12px;color:var(--text);margin:4px 0"><strong>A · One script per playbook</strong> — set <code>binding</code> to a fixed name below. Name each Zabbix script after the playbook; the operator picks from the menu.</div>
          <div style="font-size:12px;color:var(--text);margin:8px 0 4px"><strong>B · One script, dropdown</strong> — set parameter <code>binding</code> = <code>&#123;MANUALINPUT&#125;</code>, then in the script's <em>Advanced configuration</em> enable <em>User input → Dropdown</em> and paste these options:</div>
          <div class="zs-code"><code>{{ bindingNames || '(create a binding first)' }}</code><button class="btn-pill btn-pill-outline" @click="copy(bindingNames)">Copy</button></div>
        </div>
      </div>
    </div>

    <PluginList
      class="u-mt-16"
      title="Webhook plugins"
      description="Trigger sources turn an inbound webhook payload into a job run. The built-in Zabbix webhook path is handled by zabbix-integration-service directly and needs no plugin; this axis is where an additional inbound source (a different monitoring system, a CI callback) plugs in."
      empty-text="No trigger-source plugins loaded — the built-in Zabbix webhook above still works."
      :axes="['triggers']"
      :plugins="plugins"
      :can-toggle="auth.isSuperAdmin"
      @changed="$emit('changed')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import api from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import PluginList from '@/components/integration/PluginList.vue'

defineProps<{ plugins: any[] }>()
defineEmits<{ (e: 'changed'): void }>()

const auth = useAuthStore()
const origin = window.location.origin
const webhookUrl = `${origin}/webhook/zabbix/execute`

const bindings = ref<any[]>([])
const bindingNames = computed(() => bindings.value.map((b: any) => b.name).join(','))

const webhookParams = [
  `url         ${webhookUrl}`,
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

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text) } catch { /* clipboard blocked */ }
}

onMounted(async () => {
  try { bindings.value = (await api.get('/trigger-bindings')).data }
  catch { bindings.value = [] }
})
</script>

<style scoped>
.int-grid { display: flex; flex-direction: column; gap: 12px; }
.int-row { display: flex; align-items: center; gap: 12px; font-size: 14px; }
.int-k { flex: 0 0 130px; color: var(--text2); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
.int-link { color: var(--accent2); }
.note { margin-top: 18px; color: var(--text2); font-size: 12px; line-height: 1.6; }
code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.zs-block { border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.zs-title { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 8px; }
.zs-desc { font-size: 12px; color: var(--text2); margin: 6px 0 10px; }
.zs-code { display: flex; align-items: flex-start; gap: 8px; background: var(--bg3); border-radius: 6px; padding: 10px 12px; margin-top: 6px; }
.zs-code code, .zs-code pre { flex: 1; color: var(--text); font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.zs-code .btn-pill { flex-shrink: 0; }
</style>
