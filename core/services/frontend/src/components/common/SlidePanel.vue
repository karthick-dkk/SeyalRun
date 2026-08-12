<template>
  <Teleport to="body">
    <Transition name="dlg-fade">
      <div v-if="modelValue" class="dlg-backdrop" @click.self="emit('close')" />
    </Transition>
    <Transition name="dlg-rise">
      <div v-if="modelValue" class="dlg-wrap" @click.self="emit('close')">
        <div class="dlg-box" :style="{ maxWidth: width + 'px' }" role="dialog" :aria-label="title">

          <!-- Header -->
          <div class="dlg-head">
            <div class="dlg-head-content">
              <h2 class="dlg-title">{{ title }}</h2>
              <p v-if="subtitle" class="dlg-subtitle">{{ subtitle }}</p>
            </div>
            <div class="dlg-head-actions">
              <slot name="header-actions" />
              <button class="dlg-close" @click="emit('close')" title="Close (Esc)" aria-label="Close">
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                  <path d="M1 1L12 12M12 1L1 12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                </svg>
              </button>
            </div>
          </div>

          <!-- Body -->
          <div class="dlg-body">
            <slot />
          </div>

          <!-- Footer -->
          <div v-if="hasFooter" class="dlg-footer">
            <slot name="footer" />
          </div>

        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, useSlots, onMounted, onBeforeUnmount } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title: string
  subtitle?: string
  width?: number
}>(), { width: 520 })

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:modelValue', v: boolean): void
}>()

const slots = useSlots()
const hasFooter = computed(() => !!slots.footer)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.modelValue) emit('close')
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
/* ── Backdrop ───────────────────────────────────────────────────────────── */
.dlg-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  z-index: 500;
  backdrop-filter: blur(2px);
}

/* ── Right-anchored wrapper (compact side panel, vertically centered) ─────── */
.dlg-wrap {
  position: fixed;
  inset: 0;
  z-index: 501;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 24px;
}

/* ── Dialog box ─────────────────────────────────────────────────────────── */
.dlg-box {
  width: 100%;
  max-height: calc(100vh - 48px);
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 16px 50px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}

@media (max-width: 560px) {
  .dlg-wrap { padding: 0; }
  .dlg-box { max-width: 100vw !important; max-height: 100vh; height: 100vh; border-radius: 0; border: none; }
}

/* ── Header ─────────────────────────────────────────────────────────────── */
.dlg-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 18px 20px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 12px;
  background: var(--bg3);
}

.dlg-head-content { min-width: 0; flex: 1; }

.dlg-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  margin: 0;
  line-height: 1.3;
}

.dlg-subtitle {
  font-size: 12px;
  color: var(--text2);
  margin: 4px 0 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dlg-head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding-top: 1px;
}

.dlg-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--text2);
  cursor: pointer;
  transition: color 0.12s, border-color 0.12s, background 0.12s;
  flex-shrink: 0;
}
.dlg-close:hover {
  color: var(--text);
  border-color: var(--border);
  background: var(--border);
}

/* ── Body ───────────────────────────────────────────────────────────────── */
.dlg-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px;
}
.dlg-body::-webkit-scrollbar { width: 5px; }
.dlg-body::-webkit-scrollbar-track { background: transparent; }
.dlg-body::-webkit-scrollbar-thumb { background: rgba(120,120,120,0.35); border-radius: 3px; }

/* ── Footer ─────────────────────────────────────────────────────────────── */
.dlg-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  background: var(--bg3);
}

/* ── Transitions ────────────────────────────────────────────────────────── */
.dlg-fade-enter-active, .dlg-fade-leave-active { transition: opacity 0.18s ease; }
.dlg-fade-enter-from, .dlg-fade-leave-to { opacity: 0; }

.dlg-rise-enter-active, .dlg-rise-leave-active {
  transition: opacity 0.2s ease, transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}
.dlg-rise-enter-from, .dlg-rise-leave-to {
  opacity: 0;
  transform: translateX(28px);
}
</style>
