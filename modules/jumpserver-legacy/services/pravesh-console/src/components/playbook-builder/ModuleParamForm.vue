<template>
  <div class="param-form">
    <div v-for="param in params" :key="param.name" class="param-row">
      <label class="param-label">
        {{ param.name }}
        <span v-if="param.required" class="required">*</span>
        <span class="param-type">{{ param.type }}</span>
      </label>
      <p class="param-desc">{{ param.description }}</p>

      <!-- Boolean -->
      <div v-if="param.type === 'bool'" class="toggle-wrap">
        <input
          type="checkbox"
          :id="`param-${param.name}`"
          :checked="modelValue[param.name] === true || modelValue[param.name] === 'true'"
          @change="update(param.name, ($event.target as HTMLInputElement).checked)"
        />
        <label :for="`param-${param.name}`">{{ modelValue[param.name] ? 'true' : 'false' }}</label>
      </div>

      <!-- Choices -->
      <select
        v-else-if="param.choices?.length"
        :value="modelValue[param.name] ?? param.default ?? ''"
        @change="update(param.name, ($event.target as HTMLSelectElement).value)"
        class="prv-select"
      >
        <option value="">-- select --</option>
        <option v-for="c in param.choices" :key="c" :value="c">{{ c }}</option>
      </select>

      <!-- Integer -->
      <input
        v-else-if="param.type === 'int'"
        type="number"
        class="prv-input"
        :value="modelValue[param.name] ?? param.default ?? ''"
        @input="update(param.name, parseInt(($event.target as HTMLInputElement).value) || 0)"
      />

      <!-- List -->
      <textarea
        v-else-if="param.type === 'list'"
        class="prv-input prv-textarea"
        :value="Array.isArray(modelValue[param.name]) ? (modelValue[param.name] as string[]).join('\n') : (modelValue[param.name] as string ?? '')"
        :placeholder="param.example ?? 'one item per line'"
        @input="update(param.name, ($event.target as HTMLTextAreaElement).value.split('\n').filter(Boolean))"
        rows="3"
      />

      <!-- String / path / default -->
      <input
        v-else
        type="text"
        class="prv-input"
        :value="(modelValue[param.name] as string) ?? param.default ?? ''"
        :placeholder="param.example ?? ''"
        @input="update(param.name, ($event.target as HTMLInputElement).value)"
      />
    </div>
    <div v-if="params.length === 0" class="no-params">No parameters for this module.</div>
  </div>
</template>

<script setup lang="ts">
import type { ParamSchema } from '@/types'

const props = defineProps<{
  params: ParamSchema[]
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

function update(name: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}
</script>

<style scoped>
.param-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.param-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.param-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  font-family: var(--font-mono);
}
.required  { color: var(--error); }
.param-type { font-size: 10px; color: var(--text-muted); background: var(--bg-subtle); padding: 1px 6px; border-radius: var(--radius-sm); }
.param-desc { font-size: 11px; color: var(--text-muted); }

.prv-input, .prv-select, .prv-textarea {
  background: var(--bg-base);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  padding: var(--space-2) var(--space-3);
  outline: none;
  width: 100%;
  transition: border-color var(--transition);
}
.prv-input:focus, .prv-select:focus, .prv-textarea:focus { border-color: var(--accent); }

.toggle-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  color: var(--text);
}

.no-params { color: var(--text-muted); font-size: 12px; text-align: center; padding: var(--space-4); }
</style>
