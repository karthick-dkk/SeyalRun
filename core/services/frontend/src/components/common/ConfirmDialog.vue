<template>
  <Teleport to="body">
    <div v-if="state.visible" class="modal-overlay" style="z-index:600" @click.self="reject">
      <div class="modal" style="width:min(420px,94vw)">
        <div class="modal-header">
          {{ state.title }}
          <button class="btn btn-sm btn-icon" @click="reject">✕</button>
        </div>
        <div class="modal-body" style="font-size:14px;line-height:1.6;white-space:pre-line">{{ state.message }}</div>
        <div class="modal-footer">
          <button class="btn" @click="reject">{{ state.cancelLabel }}</button>
          <button :class="['btn', state.danger ? 'btn-danger' : 'btn-primary']" @click="accept">
            {{ state.confirmLabel }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useConfirm } from '../../composables/useConfirm'
const { state, accept, reject } = useConfirm()
</script>
