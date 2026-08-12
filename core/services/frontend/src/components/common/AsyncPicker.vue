<template>
  <div class="async-picker" :class="{ 'is-open': showDropdown }">
    <div class="picker-control" @click="focusInput">
      <span v-for="item in modelValue" :key="item.id" class="chip">
        <span class="chip-label">{{ item.label }}</span>
        <button type="button" class="chip-remove" @click.stop="removeItem(item)" aria-label="Remove">✕</button>
      </span>
      <input
        ref="inputEl"
        v-model="query"
        class="picker-input"
        type="text"
        :placeholder="modelValue.length ? '' : placeholder"
        @focus="onFocus"
        @input="onInput"
        @keydown.down.prevent="moveHighlight(1)"
        @keydown.up.prevent="moveHighlight(-1)"
        @keydown.enter.prevent="selectHighlighted"
        @keydown.escape="onEscape"
        @keydown.backspace="onBackspace"
      />
    </div>

    <div v-if="showDropdown" class="picker-dropdown">
      <div v-if="loading" class="picker-status">
        <span class="spinner"></span> Searching…
      </div>
      <template v-else>
        <div
          v-for="(r, idx) in results"
          :key="r.id"
          class="picker-option"
          :class="{ highlighted: idx === highlightIndex }"
          @mousedown.prevent="selectResult(r)"
          @mouseenter="highlightIndex = idx"
        >
          <span class="option-label">{{ r.label }}</span>
          <span v-if="r.sublabel" class="option-sublabel">{{ r.sublabel }}</span>
        </div>
        <div v-if="!results.length && hasSearched" class="picker-status">No results</div>
        <div v-else-if="!results.length && !hasSearched" class="picker-status">Type to search…</div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

export interface PickerItem {
  id: string
  label: string
  sublabel?: string
}

const props = withDefaults(defineProps<{
  modelValue: PickerItem[]
  searchFn: (query: string) => Promise<PickerItem[]>
  placeholder?: string
  multiple?: boolean
  maxItems?: number
}>(), {
  placeholder: 'Search…',
  multiple: true,
  maxItems: undefined,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: PickerItem[]): void
}>()

const inputEl = ref<HTMLInputElement | null>(null)
const query = ref('')
const results = ref<PickerItem[]>([])
const loading = ref(false)
const showDropdown = ref(false)
const hasSearched = ref(false)
const highlightIndex = ref(-1)

let debounceTimer: ReturnType<typeof setTimeout> | null = null
let requestSeq = 0

function focusInput() {
  inputEl.value?.focus()
}

function onFocus() {
  showDropdown.value = true
  if (!hasSearched.value && !query.value) {
    runSearch('')
  }
}

function onInput() {
  showDropdown.value = true
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    runSearch(query.value)
  }, 300)
}

async function runSearch(q: string) {
  const seq = ++requestSeq
  loading.value = true
  try {
    const r = await props.searchFn(q)
    if (seq !== requestSeq) return // stale response — ignore
    results.value = r
    hasSearched.value = true
    highlightIndex.value = r.length ? 0 : -1
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function moveHighlight(delta: number) {
  if (!results.value.length) return
  const len = results.value.length
  highlightIndex.value = (highlightIndex.value + delta + len) % len
}

function selectHighlighted() {
  const r = results.value[highlightIndex.value]
  if (r) selectResult(r)
}

function isAtMax(): boolean {
  return !!props.maxItems && props.modelValue.length >= props.maxItems
}

function selectResult(r: PickerItem) {
  if (props.multiple) {
    if (props.modelValue.some(i => i.id === r.id)) {
      clearAndRefocus()
      return
    }
    if (isAtMax()) {
      clearAndRefocus()
      return
    }
    emit('update:modelValue', [...props.modelValue, r])
    clearAndRefocus()
  } else {
    emit('update:modelValue', [r])
    query.value = ''
    closeDropdown()
  }
}

function clearAndRefocus() {
  query.value = ''
  results.value = []
  hasSearched.value = false
  nextTick(() => focusInput())
}

function removeItem(item: PickerItem) {
  emit('update:modelValue', props.modelValue.filter(i => i.id !== item.id))
}

function onBackspace() {
  if (!query.value && props.modelValue.length) {
    emit('update:modelValue', props.modelValue.slice(0, -1))
  }
}

function closeDropdown() {
  showDropdown.value = false
  highlightIndex.value = -1
}

// Only swallow ESC while the dropdown is open; otherwise let it bubble so a
// surrounding dialog's global ESC-to-close still works.
function onEscape(e: KeyboardEvent) {
  if (showDropdown.value) {
    e.stopPropagation()
    closeDropdown()
  }
}

function onClickOutside(e: MouseEvent) {
  const root = inputEl.value?.closest('.async-picker')
  if (root && !root.contains(e.target as Node)) {
    closeDropdown()
  }
}

watch(showDropdown, (open) => {
  if (open) {
    document.addEventListener('mousedown', onClickOutside)
  } else {
    document.removeEventListener('mousedown', onClickOutside)
  }
})
</script>

<style scoped>
.async-picker {
  position: relative;
  width: 100%;
}

.picker-control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 5px 8px;
  cursor: text;
  transition: border-color 0.15s;
}

.async-picker.is-open .picker-control,
.picker-control:focus-within {
  border-color: var(--accent2);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: rgba(59, 130, 246, 0.16);
  color: var(--text);
  border-radius: 12px;
  padding: 2px 6px 2px 10px;
  font-size: 12px;
  line-height: 1.6;
}

.chip-label {
  white-space: nowrap;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-remove {
  background: none;
  border: none;
  color: var(--text2);
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.chip-remove:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.08);
}

.picker-input {
  flex: 1;
  min-width: 80px;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  padding: 3px 4px;
}

.picker-input::placeholder {
  color: var(--text2);
}

.picker-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 50;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  max-height: 240px;
  overflow-y: auto;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}

.picker-option {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text);
}

.picker-option.highlighted,
.picker-option:hover {
  background: var(--bg3);
}

.option-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.option-sublabel {
  color: var(--text2);
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}

.picker-status {
  padding: 10px 12px;
  font-size: 12px;
  color: var(--text2);
  display: flex;
  align-items: center;
  gap: 8px;
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--border);
  border-top-color: var(--accent2);
  border-radius: 50%;
  animation: async-picker-spin 0.7s linear infinite;
}

@keyframes async-picker-spin {
  to { transform: rotate(360deg); }
}
</style>
