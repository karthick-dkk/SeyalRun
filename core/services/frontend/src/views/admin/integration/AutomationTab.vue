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
const galaxy = reactive({ server: '', api_key: '', collections: '' })

const savingGalaxy = ref(false); const galaxyMsg = ref(''); const galaxyErr = ref(false)


async function loadState() {
  try {
    const { data } = await api.get('/integrations/state')
    cfg.value = data.config || {}
    galaxy.server = cfg.value.galaxy?.server || ''
    galaxy.api_key = ''
    galaxy.collections = cfg.value.galaxy?.collections || ''
  } catch { cfg.value = {} }
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
