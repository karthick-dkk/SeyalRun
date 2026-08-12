<template>
  <router-view />
  <ToastContainer />
</template>

<script setup lang="ts">
import { watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import ToastContainer from '@/components/design/ToastContainer.vue'

const route = useRoute()

watchEffect(() => {
  const isTerminal = route.meta.fullscreen === true
  document.body.style.overflow = isTerminal ? 'hidden' : ''

  // Switch favicon + title based on page
  const favicon = document.getElementById('favicon') as HTMLLinkElement | null
  if (favicon) {
    favicon.href = isTerminal ? '/favicon-terminal.svg' : '/favicon-app.svg'
  }
  document.title = isTerminal ? 'SeyalRun — Terminal' : 'SeyalRun Console'
})
</script>
