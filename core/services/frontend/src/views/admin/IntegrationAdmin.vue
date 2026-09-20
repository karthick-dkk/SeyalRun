<template>
  <div>
    <div class="int-tabs">
      <button
        v-for="t in TABS"
        :key="t.id"
        class="int-tab"
        :class="{ active: tab === t.id }"
        @click="tab = t.id"
      >
        {{ t.label }}
        <span v-if="countFor(t)" class="int-tab-count">{{ countFor(t) }}</span>
      </button>
      <div class="int-tabs-spacer" />
      <button class="btn btn-sm" :disabled="loading" @click="loadPlugins">
        {{ loading ? 'Loading…' : 'Refresh plugins' }}
      </button>
    </div>

    <div v-if="unreachable.length" class="int-warn">
      Could not read plugins from: <strong>{{ unreachable.join(', ') }}</strong>.
      That list is incomplete until those services answer — this is not the same as “no plugins”.
    </div>

    <ZabbixTab v-if="tab === 'zabbix'" :plugins="plugins" @changed="loadPlugins" />
    <JumpServerTab v-else-if="tab === 'jumpserver'" :plugins="plugins" @changed="loadPlugins" />
    <WebhookTab v-else-if="tab === 'webhook'" :plugins="plugins" @changed="loadPlugins" />
    <AutomationTab v-else :plugins="plugins" @changed="loadPlugins" />

    <p class="int-foot">
      Plugins ship inside the service images and are discovered at startup — nothing on this page
      uploads or installs code into a running deployment. What it changes is whether SeyalRun
      <em>uses</em> a plugin it already has, plus the connection settings for the integrations that
      need them.
    </p>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import api from '@/api/client'
import ZabbixTab from './integration/ZabbixTab.vue'
import JumpServerTab from './integration/JumpServerTab.vue'
import WebhookTab from './integration/WebhookTab.vue'
import AutomationTab from './integration/AutomationTab.vue'

/**
 * Integration hub. The plugin catalog is fetched ONCE here and handed to each tab,
 * because it is a single cross-service aggregate — letting four tabs each fan out
 * to five services would turn a tab click into twenty internal requests.
 */
const TABS = [
  { id: 'zabbix', label: 'Zabbix Plugins', axes: ['triggers'] },
  { id: 'jumpserver', label: 'JumpServer Plugins', axes: ['brokers'] },
  { id: 'webhook', label: 'Webhook Plugins', axes: ['triggers'] },
  { id: 'automation', label: 'Automation Plugins', axes: ['executors'] },
]

const tab = ref('zabbix')
const plugins = ref<any[]>([])
const unreachable = ref<string[]>([])
const loading = ref(false)

function countFor(t: any): number {
  if (t.id === 'jumpserver') {
    return plugins.value.filter(p => p.name.startsWith('jumpserver')).length
  }
  if (t.id === 'zabbix') {
    return plugins.value.filter(p => p.name.startsWith('zabbix')).length
  }
  return plugins.value.filter(p => t.axes.includes(p.axis)).length
}

async function loadPlugins() {
  loading.value = true
  try {
    const { data } = await api.get('/integrations/plugins')
    plugins.value = data.plugins || []
    unreachable.value = data.unreachable || []
  } catch {
    plugins.value = []
    unreachable.value = []
  } finally {
    loading.value = false
  }
}

onMounted(loadPlugins)
</script>

<style scoped>
.int-tabs { display: flex; align-items: center; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
.int-tabs-spacer { flex: 1; }
.int-tab {
  background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer;
  color: var(--text2); font-size: 13px; font-weight: 500; padding: 9px 14px;
  display: flex; align-items: center; gap: 7px;
}
.int-tab:hover { color: var(--text); }
.int-tab.active { color: var(--text); border-bottom-color: var(--accent); }
.int-tab-count { font-size: 11px; background: var(--bg3); border-radius: 9px; padding: 0 6px; color: var(--text2); }
.int-warn {
  font-size: 12px; color: var(--text); background: var(--bg3);
  border: 1px solid var(--border); border-left: 3px solid var(--warn, #c98a1b);
  border-radius: 6px; padding: 9px 12px; margin-bottom: 16px; line-height: 1.6;
}
.int-foot { color: var(--text2); font-size: 12px; line-height: 1.6; margin-top: 18px; }
</style>
