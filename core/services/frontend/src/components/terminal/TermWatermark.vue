<template>
  <div class="wm" aria-hidden="true">
    <div v-for="i in 12" :key="i" class="wm-tile">{{ text }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * Session watermark — Increment 3 (deterrent layer).
 *
 * Overlays the operator's identity, the session id and a clock across the
 * terminal. It stops nothing technically; its whole function is that a
 * photograph or screen recording of the pane carries who was looking at it and
 * when. That is a deterrent against the one exfiltration path a PAM cannot
 * intercept — a camera pointed at the screen — and it is what makes a leaked
 * screenshot attributable after the fact.
 *
 * pointer-events: none throughout, so it can never intercept a click or a
 * selection; the terminal underneath behaves exactly as if it were not here.
 */
import { computed, onBeforeUnmount, ref } from 'vue'

const props = defineProps<{ username: string; sessionId: string }>()

const now = ref(new Date())
const timer = setInterval(() => { now.value = new Date() }, 30_000)
onBeforeUnmount(() => clearInterval(timer))

const text = computed(() =>
  `${props.username} · ${props.sessionId.slice(0, 8)} · ${now.value.toISOString().slice(0, 16).replace('T', ' ')}`,
)
</script>

<style scoped>
.wm {
  position: absolute; inset: 0;
  pointer-events: none;          /* never intercepts input or text selection */
  overflow: hidden;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  align-content: space-around;
  z-index: 5;
  user-select: none;
}
.wm-tile {
  font: 600 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(255, 255, 255, 0.055);
  transform: rotate(-24deg);
  white-space: nowrap;
  text-align: center;
  padding: 18px 0;
}
/* The watermark must stay legible enough to read off a photograph on a light
   theme too, where a white overlay would vanish entirely. */
@media (prefers-color-scheme: light) {
  .wm-tile { color: rgba(0, 0, 0, 0.06); }
}
</style>
