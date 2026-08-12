<template>
  <div class="yaml-preview">
    <div class="yaml-toolbar">
      <span class="yaml-title">YAML Preview</span>
      <div class="yaml-actions">
        <span class="line-count">{{ lineCount }} lines</span>
        <button @click="copy" title="Copy YAML">⎘ Copy</button>
      </div>
    </div>
    <div ref="editorEl" class="yaml-editor" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'

const props = defineProps<{ content: string }>()

const editorEl = ref<HTMLElement>()
let view: EditorView | null = null

const lineCount = computed(() => props.content.split('\n').length)

import { computed } from 'vue'

onMounted(() => {
  view = new EditorView({
    doc: props.content,
    extensions: [
      basicSetup,
      yaml(),
      oneDark,
      EditorView.editable.of(false),
      EditorView.theme({
        '&': { background: '#010409', height: '100%' },
        '.cm-content': { fontFamily: 'var(--font-mono)', fontSize: '12px' },
        '.cm-gutters': { background: '#010409', borderRight: '1px solid #21262d' },
      }),
    ],
    parent: editorEl.value!,
  })
})

watch(() => props.content, (val) => {
  if (!view) return
  view.dispatch({
    changes: { from: 0, to: view.state.doc.length, insert: val },
  })
})

onUnmounted(() => view?.destroy())

function copy() {
  navigator.clipboard.writeText(props.content)
}
</script>

<style scoped>
.yaml-preview {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  height: 100%;
}

.yaml-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-4);
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.yaml-title { font-family: var(--font-mono); font-size: 12px; color: var(--text-muted); }

.yaml-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.line-count { font-family: var(--font-mono); font-size: 11px; color: var(--text-subtle); }

.yaml-actions button {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  font-size: 11px;
  cursor: pointer;
  transition: all var(--transition);
}
.yaml-actions button:hover { color: var(--text); border-color: var(--border-muted); }

.yaml-editor {
  flex: 1;
  overflow: auto;
  min-height: 300px;
}
</style>
