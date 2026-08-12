<template>
  <teleport to="body">
    <div class="toast-container">
      <transition-group name="toast">
        <div
          v-for="toast in ui.toasts"
          :key="toast.id"
          class="toast"
          :class="`toast--${toast.type}`"
          @click="ui.removeToast(toast.id)"
        >
          <span class="toast-icon">{{ icons[toast.type] }}</span>
          <span class="toast-msg">{{ toast.message }}</span>
        </div>
      </transition-group>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const icons = { success: '✓', error: '✗', warning: '!', info: 'i' }
</script>

<style scoped>
.toast-container {
  position: fixed;
  bottom: var(--space-6);
  right: var(--space-6);
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--bg-overlay);
  border: 1px solid var(--border-muted);
  font-size: 13px;
  color: var(--text);
  min-width: 280px;
  max-width: 400px;
  pointer-events: all;
  cursor: pointer;
  box-shadow: var(--shadow-md);
}

.toast--success { border-color: var(--success); }
.toast--error   { border-color: var(--error); }
.toast--warning { border-color: var(--warning); }
.toast--info    { border-color: var(--accent); }

.toast-icon { font-family: var(--font-mono); font-weight: 700; }
.toast--success .toast-icon { color: var(--success); }
.toast--error   .toast-icon { color: var(--error); }
.toast--warning .toast-icon { color: var(--warning); }
.toast--info    .toast-icon { color: var(--accent); }

.toast-enter-active, .toast-leave-active { transition: all 200ms ease; }
.toast-enter-from { opacity: 0; transform: translateY(16px); }
.toast-leave-to   { opacity: 0; transform: translateX(100%); }
</style>
