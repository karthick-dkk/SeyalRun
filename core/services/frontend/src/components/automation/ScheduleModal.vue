<template>
  <div v-if="modelValue" class="modal-overlay" @click.self="close">
    <div class="modal">
      <div class="modal-header">
        <div class="u-fs-15_fw-700">{{ isEdit ? 'Edit Schedule' : 'New Schedule' }}</div>
        <button class="btn btn-sm btn-icon" @click="close">✕</button>
      </div>
      <div class="modal-body sm-body">
        <div class="form-group">
          <label class="form-label">Schedule Name <span class="text-danger">*</span></label>
          <input v-model="form.name" class="input" placeholder="e.g. Nightly Deploy" />
        </div>
        <div class="form-group">
          <label class="form-label">Job Template <span class="text-danger">*</span></label>
          <select v-model="form.job_template_id" class="input">
            <option value="">— Select Template —</option>
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Cron Expression <span class="text-danger">*</span></label>
          <input v-model="form.cron_expression" class="input" placeholder="0 2 * * *" />
          <div class="sm-hint">{{ cronHuman(form.cron_expression) }}</div>
        </div>
        <div class="form-group">
          <label class="form-label">Status</label>
          <select v-model="form.enabled" class="input">
            <option :value="true">Active</option>
            <option :value="false">Disabled</option>
          </select>
        </div>
        <div v-if="error" class="sm-error">{{ error }}</div>
      </div>
      <div class="modal-footer">
        <button class="btn" @click="close">Cancel</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? 'Saving…' : (isEdit ? 'Save' : 'Create') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Create or edit a schedule — extracted from AutomationView.vue.
 *
 * Owns its own form, validation, request and error. The parent supplies the
 * template list and is told when something was saved, so it can reload; it does
 * not reach into this form and this does not reach into its state.
 */
import { reactive, ref, watch } from 'vue'
import api from '@/api/client'

const props = defineProps<{
  modelValue: boolean
  templates: any[]
  /** Existing schedule to edit; null/undefined creates a new one. */
  editing?: any | null
  /** Shared cron describer, so the modal and the list read identically. */
  cronHuman: (expr: string) => string
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

// cron pre-filled to match the original form: a new schedule starts from a
// working nightly expression rather than an empty box the user must decode.
const blank = () => ({ name: '', job_template_id: '', cron_expression: '0 2 * * *', enabled: true })
const form = reactive(blank())
const saving = ref(false)
const error = ref('')
const isEdit = ref(false)

// Reset on OPEN rather than on close: leaving a stale form behind means the next
// "New Schedule" opens pre-filled with the last edit, which is how someone
// overwrites a schedule they meant to create.
watch(() => props.modelValue, (open) => {
  if (!open) return
  error.value = ''
  saving.value = false
  isEdit.value = !!props.editing
  Object.assign(form, blank(), props.editing
    ? {
        name: props.editing.name,
        job_template_id: props.editing.job_template_id,
        cron_expression: props.editing.cron_expression,
        enabled: props.editing.enabled,
      }
    : {})
})

function close() { emit('update:modelValue', false) }

async function save() {
  error.value = ''
  if (!form.name.trim()) { error.value = 'Name required.'; return }
  if (!form.job_template_id) { error.value = 'Job template required.'; return }
  if (!form.cron_expression.trim()) { error.value = 'Cron expression required.'; return }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      job_template_id: form.job_template_id,
      cron_expression: form.cron_expression,
      enabled: form.enabled,
      params_override: {},
    }
    if (isEdit.value) await api.put(`/schedules/${props.editing.id}`, payload)
    else await api.post('/schedules', payload)
    emit('saved')
    close()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Save failed'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.sm-body { display: flex; flex-direction: column; gap: 14px; }
.sm-hint { font-size: 12px; color: var(--text2); margin-top: 4px; }
.sm-error {
  color: var(--danger); font-size: 13px; padding: 10px;
  background: rgba(248, 81, 73, 0.08); border-radius: 6px;
  border: 1px solid rgba(248, 81, 73, 0.3);
}
</style>
