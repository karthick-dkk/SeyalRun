<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal" role="dialog" :aria-label="`Reboot ${hostname}`" style="width:min(480px,96vw)">
      <div class="modal-header"><span>Reboot {{ hostname }}</span><button class="btn sm ib g" type="button" aria-label="Close" @click="emit('close')">×</button></div>
      <div class="modal-body stack" style="gap:12px">
        <p style="margin:0">The host restarts now and is unavailable until it is back. Open sessions on it are cut. SeyalRun waits up to 15 minutes for it to return, then collects its facts again.</p>
        <p class="muted" style="margin:0">To reboot many hosts in waves, inside a maintenance window, use a patch rollout instead.</p>
        <label for="rb-reason" class="ct">Reason</label>
        <textarea id="rb-reason" v-model="reason" class="in" rows="3" style="height:auto;padding:8px 10px" placeholder="Why now? For example: kernel update installed"></textarea>
        <div v-if="err" class="callout" style="--c:var(--bad)" role="alert">{{ err }}</div>
      </div>
      <div class="modal-footer">
        <button class="btn" type="button" @click="emit('close')">Cancel</button>
        <button class="btn p" type="button" :disabled="busy || reason.trim().length < 3" @click="submit">{{ busy ? 'Requesting…' : 'Reboot now' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import api from '@/api/client'
import { detailOf } from './posture'

const props = defineProps<{ hostId: string; hostname: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'queued', message: string): void }>()
const reason = ref('')
const busy = ref(false)
const err = ref('')

async function submit() {
  busy.value = true; err.value = ''
  try {
    const r = (await api.post(`/posture/hosts/${props.hostId}/reboot`, { reason: reason.value.trim() })).data
    emit('queued', r?.status === 'awaiting_approval'
      ? `The reboot of ${props.hostname} waits for another admin's approval (Approvals).`
      : `Rebooting ${props.hostname}. It shows under Package operations on the host page.`)
  } catch (e: any) {
    err.value = detailOf(e, 'The reboot could not be requested.')
  } finally { busy.value = false }
}
</script>
