<template>
  <div v-if="modelValue" class="modal-overlay" @click.self="close">
    <div class="modal">
      <div class="modal-header">
        <div class="u-fs-15_fw-700">Import {{ isScript ? 'Script' : 'Playbook' }} from GitHub</div>
        <button class="btn btn-sm btn-icon" @click="close">✕</button>
      </div>
      <div class="modal-body gi-body">
        <div class="form-group">
          <label class="form-label">GitHub URL</label>
          <input
            v-model="url"
            class="input"
            :placeholder="isScript
              ? 'https://github.com/owner/repo/blob/main/script.sh'
              : 'https://github.com/owner/repo/blob/main/playbook.yml'"
            @keydown.enter="submit"
          />
          <div class="gi-hint">
            A github.com file link (blob URL) or a raw.githubusercontent.com link. Replaces the
            current content below — nothing is saved until you click Save on the template. The URL
            is kept with the template afterward for reference.
          </div>
        </div>
        <div v-if="error" class="gi-error">{{ error }}</div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="close">Cancel</button>
        <button class="btn btn-primary" :disabled="loading || !url.trim()" @click="submit">
          {{ loading ? 'Importing…' : 'Import' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Import a playbook or script from GitHub — extracted from AutomationView.vue.
 *
 * Self-contained: it owns the URL, the request and its own error state, and
 * emits the imported content. The parent decides what to do with it, which is
 * why this does not touch the edit form directly — reaching into a parent's
 * reactive object from a child is how a 1,700-line view gets that way.
 */
import { computed, ref, watch } from 'vue'
import api from '@/api/client'

const props = defineProps<{ modelValue: boolean; actionType: string }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'imported', payload: { content: string; sourceUrl: string; actionType: string }): void
}>()

const url = ref('')
const loading = ref(false)
const error = ref('')

const isScript = computed(() => props.actionType === 'bash_script')

// Stale error text from a previous attempt reappearing on reopen reads as a
// failure that has not happened yet.
watch(() => props.modelValue, (open) => { if (open) error.value = '' })

function close() {
  emit('update:modelValue', false)
}

async function submit() {
  if (!url.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    const r = await api.post('/job-templates/import-playbook', { url: url.value.trim() })
    const sourceUrl = r.data.source_url || url.value.trim()
    // Detect the type from the imported file's extension so the right linter runs.
    // Without this, importing a .sh while "Ansible Playbook" was still selected ran
    // the YAML parser over real bash and reported false syntax errors — the script
    // was fine, it was never valid YAML to begin with.
    const detected = /\.(sh|bash)(\?|#|$)/.test(sourceUrl.toLowerCase())
      ? 'bash_script'
      : 'ansible_playbook'
    emit('imported', { content: r.data.content, sourceUrl, actionType: detected })
    url.value = ''
    close()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Import failed'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.gi-body { display: flex; flex-direction: column; gap: 14px; }
.gi-hint { font-size: 11.5px; color: var(--text2); margin-top: 4px; }
.gi-error {
  color: var(--danger); font-size: 13px; padding: 10px;
  background: rgba(248, 81, 73, 0.08); border-radius: 6px;
  border: 1px solid rgba(248, 81, 73, 0.3);
}
</style>
