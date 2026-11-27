<template>
  <div v-if="host" class="modal-overlay" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-label="Request access" @click.stop>
      <div class="modal-header">
        <span>Request access · {{ host.name }}</span>
        <button class="btn btn-sm" type="button" aria-label="Close" @click="emit('close')">✕</button>
      </div>
      <form class="modal-body" @submit.prevent="submit">
        <div v-if="loading" class="text-muted-sm">Loading accounts…</div>
        <template v-else-if="opts">
          <p v-if="opts.has_access" class="ra-note">You can already log in to this host — there is nothing to request.</p>
          <p v-else-if="!opts.accounts.length" class="ra-note">This host has no accounts to request. Ask an administrator to add one.</p>
          <template v-else>
            <p class="text-muted-sm ra-intro">
              An administrator reviews the request. You get an SSH login with the account you choose,
              for the time you choose, starting when it is approved.
            </p>
            <div class="form-group">
              <label class="form-label" for="ra-account">Account</label>
              <select id="ra-account" v-model="form.credential_id" class="input" required>
                <option value="" disabled>Choose an account…</option>
                <option v-for="a in opts.accounts" :key="a.id" :value="a.id" :disabled="a.pending">
                  {{ a.username }}{{ a.name && a.name !== a.username ? ` — ${a.name}` : '' }}{{ a.pending ? ' (request pending)' : '' }}
                </option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label" for="ra-duration">For how long</label>
              <select id="ra-duration" v-model="form.duration" class="input" required>
                <option v-for="d in opts.durations" :key="d.value" :value="d.value">{{ d.label }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label" for="ra-reason">Reason <span class="text-muted-sm">({{ form.reason.trim().length }}/1000)</span></label>
              <textarea id="ra-reason" v-model="form.reason" class="input" rows="4" minlength="3" maxlength="1000" required
                placeholder="What do you need to do on this host, and why?" />
            </div>
          </template>
        </template>
        <div v-if="error" class="ra-error" role="alert">{{ error }}</div>
        <div class="modal-footer ra-footer">
          <button type="button" class="btn" @click="emit('close')">Cancel</button>
          <button v-if="canSubmit" type="submit" class="btn btn-primary" :disabled="saving || !valid">
            {{ saving ? 'Sending…' : 'Send request' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import api from '@/api/client'

interface Account { id: string; name: string; username: string; pending: boolean }
interface Duration { value: string; label: string; minutes: number }
interface Options { host: { id: string; name: string }; accounts: Account[]; durations: Duration[]; has_access: boolean }

const props = defineProps<{ host: { id: string; name: string } | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'submitted'): void }>()

const opts = ref<Options | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const form = reactive({ credential_id: '', duration: '8h', reason: '' })

const canSubmit = computed(() => !!opts.value && !opts.value.has_access && opts.value.accounts.length > 0)
const valid = computed(() => {
  const n = form.reason.trim().length
  return !!form.credential_id && !!form.duration && n >= 3 && n <= 1000
})

watch(() => props.host?.id, async (id) => {
  opts.value = null
  error.value = ''
  form.credential_id = ''
  form.duration = '8h'
  form.reason = ''
  if (!id) return
  loading.value = true
  try {
    const r = await api.get('/auth/access-requests/options', { params: { host_id: id } })
    opts.value = r.data
    const firstFree = (r.data.accounts as Account[]).find(a => !a.pending)
    if (firstFree && r.data.accounts.length === 1) form.credential_id = firstFree.id
  } catch (e: any) {
    error.value = e?.response?.data?.detail || 'Could not load the accounts for this host.'
  } finally {
    loading.value = false
  }
}, { immediate: true })

async function submit() {
  if (!props.host || !valid.value) return
  saving.value = true
  error.value = ''
  try {
    await api.post('/auth/access-requests', {
      host_id: props.host.id,
      credential_id: form.credential_id,
      duration: form.duration,
      reason: form.reason.trim(),
    })
    emit('submitted')
  } catch (e: any) {
    const d = e?.response?.data?.detail
    error.value = typeof d === 'string' ? d : 'The request could not be sent.'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.ra-intro { margin: 0 0 14px; line-height: 1.5; }
.ra-note { margin: 0 0 12px; color: var(--text2); font-size: 13px; }
.ra-error { color: var(--danger); font-size: 12.5px; margin-top: 6px; }
.ra-footer { padding: 12px 0 0; border-top: none; }
textarea.input { resize: vertical; font-family: inherit; }
</style>
