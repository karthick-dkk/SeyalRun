<template>
  <div class="terminal-wrapper">
    <div class="terminal-toolbar">
      <span class="terminal-title">{{ title }}</span>
      <div class="terminal-actions">
        <button @click="scrollToBottom" title="Jump to bottom">↓</button>
        <button @click="copyOutput" title="Copy output">⎘</button>
        <button @click="downloadOutput" title="Download .txt">⬇</button>
      </div>
    </div>
    <div ref="outputEl" class="terminal-body">
      <div
        v-for="(line, i) in lines"
        :key="i"
        class="terminal-line"
        :class="getLineClass(line)"
      >{{ line }}</div>
      <div v-if="!done" class="terminal-cursor">█</div>
    </div>
    <div class="terminal-footer">
      <span v-if="done" :class="exitCode === 0 ? 'text-success' : 'text-error'">
        {{ exitCode === 0 ? '✓ completed' : `✗ exit ${exitCode}` }}
      </span>
      <span v-else class="text-warning">running…</span>
      <span class="text-muted">{{ lines.length }} lines</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'

const props = defineProps<{
  lines: string[]
  done: boolean
  exitCode?: number | null
  title?: string
}>()

const outputEl = ref<HTMLElement>()

function getLineClass(line: string): string {
  if (/FAILED|ERROR|fatal/.test(line)) return 'line-error'
  if (/SKIPPING|SKIP/.test(line)) return 'line-skip'
  if (/ok:|changed:|PLAY |TASK /.test(line)) return 'line-ok'
  if (/^\[ERROR\]/.test(line)) return 'line-error'
  return ''
}

function scrollToBottom() {
  nextTick(() => {
    if (outputEl.value) {
      outputEl.value.scrollTop = outputEl.value.scrollHeight
    }
  })
}

function copyOutput() {
  navigator.clipboard.writeText(props.lines.join('\n'))
}

function downloadOutput() {
  const blob = new Blob([props.lines.join('\n')], { type: 'text/plain' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'job-output.txt'
  a.click()
}

watch(() => props.lines.length, scrollToBottom)
</script>

<style scoped>
.terminal-wrapper {
  display: flex;
  flex-direction: column;
  background: #010409;
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.terminal-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-4);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
}

.terminal-title {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-muted);
}

.terminal-actions {
  display: flex;
  gap: var(--space-2);
}

.terminal-actions button {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 2px 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition);
}
.terminal-actions button:hover { color: var(--text); border-color: var(--border-muted); }

.terminal-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4);
  min-height: 300px;
  max-height: 500px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.terminal-line { white-space: pre-wrap; word-break: break-all; }
.line-error  { color: var(--error); }
.line-ok     { color: var(--success); }
.line-skip   { color: var(--warning); }

.terminal-cursor {
  color: var(--accent);
  animation: blink 1s step-end infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

.terminal-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-2) var(--space-4);
  border-top: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 11px;
}
</style>
