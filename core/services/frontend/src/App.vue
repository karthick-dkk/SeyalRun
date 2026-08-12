<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useAuthStore } from '@/stores/auth'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const auth = useAuthStore()

// Global ESC-to-close for every `.modal-overlay` dialog. Each overlay closes on
// `@click.self`, so we simulate a backdrop click on the topmost open one. Views
// that manage their own ESC (SlidePanel, AsyncPicker, terminal panes) use other
// class names and are unaffected.
function onGlobalEsc(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  const overlays = Array.from(document.querySelectorAll<HTMLElement>('.modal-overlay'))
    .filter((el) => el.offsetParent !== null)
  const top = overlays[overlays.length - 1]
  if (top) top.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

onMounted(() => {
  // main.ts now awaits router.isReady() before mounting, so the router's own
  // beforeEach guard (including the _session handoff) has already resolved
  // auth state by the time this fires — skip the redundant /auth/session call.
  if (!auth.ready) auth.init()
  window.addEventListener('keydown', onGlobalEsc)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalEsc))
</script>

<template>
  <router-view />
  <ConfirmDialog />
</template>
