import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Toast {
  id: string
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

export const useUiStore = defineStore('ui', () => {
  const toasts = ref<Toast[]>([])
  const sidebarCollapsed = ref(false)

  function addToast(type: Toast['type'], message: string, duration = 4000) {
    const id = Math.random().toString(36).slice(2)
    toasts.value.push({ id, type, message })
    setTimeout(() => removeToast(id), duration)
  }

  function removeToast(id: string) {
    const i = toasts.value.findIndex((t) => t.id === id)
    if (i !== -1) toasts.value.splice(i, 1)
  }

  function toast(message: string) { addToast('info', message) }
  function success(message: string) { addToast('success', message) }
  function error(message: string) { addToast('error', message, 6000) }
  function warn(message: string) { addToast('warning', message) }

  return { toasts, sidebarCollapsed, addToast, removeToast, toast, success, error, warn }
})
