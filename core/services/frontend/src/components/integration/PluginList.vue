<template>
  <div class="card">
    <div class="card-header">
      {{ title }}
      <span class="pl-count">{{ shown.length }}</span>
    </div>
    <div class="u-p-18">
      <p v-if="description" class="pl-desc">{{ description }}</p>

      <div v-if="!shown.length" class="pl-empty">
        {{ emptyText || 'No plugins loaded for this extension point.' }}
      </div>

      <div v-for="p in shown" :key="p.axis + '/' + p.name" class="pl-row">
        <div class="pl-main">
          <div class="pl-name">
            {{ p.name }}
            <span class="badge" :class="p.enabled ? 'badge-green' : 'badge-gray'">
              {{ p.enabled ? 'Enabled' : 'Disabled' }}
            </span>
            <span v-if="!p.togglable" class="badge badge-blue" :title="p.locked_reason">built-in</span>
          </div>
          <div class="pl-summary">{{ p.summary || '—' }}</div>
          <div class="pl-meta">{{ p.axis_label }} · {{ p.service }} · <code>{{ p.module }}</code></div>
        </div>

        <div class="pl-action">
          <span v-if="!p.togglable" class="pl-locked" :title="p.locked_reason">always on</span>
          <button
            v-else-if="canToggle"
            class="btn btn-sm"
            :class="p.enabled ? '' : 'btn-primary'"
            :disabled="busy === p.axis + '/' + p.name"
            @click="toggle(p)"
          >
            {{ busy === p.axis + '/' + p.name ? '…' : (p.enabled ? 'Disable' : 'Enable') }}
          </button>
          <span v-else class="pl-locked">superadmin only</span>
        </div>
      </div>

      <div v-if="error" class="pl-err">{{ error }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import api from '@/api/client'

/**
 * Renders the plugins for one or more axes, with an enable/disable switch.
 *
 * Nothing here installs anything: the list is whatever the services actually
 * loaded at startup (reported by GET /integrations/plugins), and the only write
 * is a flag saying "stop using this one". An axis the backend marked
 * `togglable: false` renders as "always on" with no control at all rather than a
 * disabled-looking button — the vault's key provider has no safe off state, and
 * a greyed-out switch would imply there is one.
 */
const props = defineProps<{
  title: string
  description?: string
  emptyText?: string
  axes: string[]
  /** Optional name allowlist — lets a tab show one plugin of a shared axis
   *  (the JumpServer tab wants the jumpserver IdP, not every IdP). */
  only?: string[]
  plugins: any[]
  canToggle: boolean
}>()

const emit = defineEmits<{ (e: 'changed'): void }>()

const busy = ref('')
const error = ref('')

const shown = computed(() => (props.plugins || [])
  .filter(p => props.axes.includes(p.axis))
  .filter(p => !props.only?.length || props.only.includes(p.name)))

async function toggle(p: any) {
  const key = `${p.axis}/${p.name}`
  busy.value = key
  error.value = ''
  try {
    await api.put(`/integrations/plugins/${p.axis}/${p.name}`, { enabled: !p.enabled })
    emit('changed')
  } catch (e: any) {
    error.value = e?.response?.data?.detail || `Could not update ${p.name}`
  } finally {
    busy.value = ''
  }
}
</script>

<style scoped>
.pl-desc { font-size: 12px; color: var(--text2); line-height: 1.6; margin: 0 0 14px; }
.pl-count { font-size: 11px; color: var(--text2); background: var(--bg3); border-radius: 10px; padding: 1px 8px; }
.pl-empty { font-size: 12px; color: var(--text2); padding: 10px 0; }
.pl-row { display: flex; align-items: flex-start; gap: 16px; padding: 12px 0; border-top: 1px solid var(--border); }
.pl-row:first-of-type { border-top: none; }
.pl-main { flex: 1; min-width: 0; }
.pl-name { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: var(--text); font-family: monospace; }
.pl-summary { font-size: 12px; color: var(--text); margin-top: 4px; }
.pl-meta { font-size: 11px; color: var(--text2); margin-top: 4px; }
.pl-meta code { background: var(--bg3); padding: 1px 5px; border-radius: 4px; }
.pl-action { flex-shrink: 0; padding-top: 2px; }
.pl-locked { font-size: 11px; color: var(--text2); }
.pl-err { font-size: 12px; color: var(--danger); margin-top: 10px; }
</style>
