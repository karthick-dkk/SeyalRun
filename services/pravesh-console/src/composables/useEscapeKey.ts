import { onMounted, onUnmounted } from 'vue'

export function useEscapeKey(handler: () => void) {
  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault()
      handler()
    }
  }
  onMounted(() => document.addEventListener('keydown', onKeyDown, { capture: true }))
  onUnmounted(() => document.removeEventListener('keydown', onKeyDown, { capture: true }))
}
