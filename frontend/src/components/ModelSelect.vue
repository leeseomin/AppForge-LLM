<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import type { ProviderModel } from '../types';

const props = withDefaults(
  defineProps<{
    models: ProviderModel[];
    modelValue: string;
    placeholder?: string;
    loading?: boolean;
  }>(),
  {
    placeholder: '모델 검색 또는 ID 입력',
    loading: false,
  },
);

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

const query = ref(props.modelValue);
const isOpen = ref(false);
const highlightedIndex = ref(0);
const inputRef = ref<HTMLInputElement | null>(null);

watch(
  () => props.modelValue,
  (val) => {
    query.value = val;
  },
);

const normalizedQuery = computed(() => query.value.trim().toLowerCase());

const filtered = computed<ProviderModel[]>(() => {
  const q = normalizedQuery.value;
  if (!q) return props.models.slice(0, 50);
  const results: Array<{ model: ProviderModel; score: number }> = [];
  for (const m of props.models) {
    const id = m.id.toLowerCase();
    const name = (m.name || '').toLowerCase();
    let score = 0;
    if (id === q) {
      score = 100;
    } else if (id.startsWith(q)) {
      score = 80;
    } else if (id.includes(q)) {
      score = 60;
    } else if (name.startsWith(q)) {
      score = 70;
    } else if (name.includes(q)) {
      score = 50;
    } else {
      continue;
    }
    results.push({ model: m, score });
  }
  results.sort((a, b) => b.score - a.score || a.model.id.localeCompare(b.model.id));
  return results.slice(0, 50).map((r) => r.model);
});

watch(filtered, () => {
  highlightedIndex.value = 0;
});

function openDropdown() {
  if (props.loading) return;
  isOpen.value = true;
  highlightedIndex.value = 0;
}

function closeDropdown() {
  isOpen.value = false;
}

function selectModel(model: ProviderModel) {
  query.value = model.id;
  emit('update:modelValue', model.id);
  closeDropdown();
  inputRef.value?.blur();
}

function onInput(event: Event) {
  query.value = (event.target as HTMLInputElement).value;
  emit('update:modelValue', query.value);
  openDropdown();
}

function onFocus() {
  openDropdown();
}

function onBlur() {
  setTimeout(() => closeDropdown(), 150);
}

function onKeydown(event: KeyboardEvent) {
  if (!isOpen.value) {
    if (event.key === 'ArrowDown' || event.key === 'Enter') {
      openDropdown();
      event.preventDefault();
    }
    return;
  }
  const items = filtered.value;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    highlightedIndex.value = Math.min(highlightedIndex.value + 1, items.length - 1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    highlightedIndex.value = Math.max(highlightedIndex.value - 1, 0);
  } else if (event.key === 'Enter') {
    event.preventDefault();
    const item = items[highlightedIndex.value];
    if (item) selectModel(item);
  } else if (event.key === 'Escape') {
    closeDropdown();
  }
}

function highlightClass(index: number): string {
  return index === highlightedIndex.value ? 'is-highlighted' : '';
}

onMounted(() => {
  query.value = props.modelValue;
});

onUnmounted(() => {
  closeDropdown();
});
</script>

<template>
  <div class="model-select" :class="{ 'is-open': isOpen, 'is-loading': loading }">
    <input
      ref="inputRef"
      :value="query"
      type="text"
      class="model-select-input"
      :placeholder="loading ? '모델 목록 로딩 중...' : placeholder"
      autocomplete="off"
      @input="onInput"
      @focus="onFocus"
      @blur="onBlur"
      @keydown="onKeydown"
    />
    <span v-if="loading" class="model-select-spinner" aria-hidden="true"></span>
    <div v-if="isOpen && !loading" class="model-select-dropdown">
      <p v-if="filtered.length === 0" class="model-select-empty">검색 결과 없음</p>
      <button
        v-for="(m, index) in filtered"
        :key="m.id"
        type="button"
        class="model-select-option"
        :class="highlightClass(index)"
        @mousedown.prevent="selectModel(m)"
        @mouseenter="highlightedIndex = index"
      >
        <span class="model-select-option-id">{{ m.id }}</span>
        <span v-if="m.name && m.name !== m.id" class="model-select-option-name">{{ m.name }}</span>
      </button>
      <p v-if="models.length > 50 && normalizedQuery" class="model-select-more">
        더 많은 결과는 검색어를 구체화하세요
      </p>
    </div>
  </div>
</template>

<style scoped>
.model-select {
  position: relative;
  width: 100%;
}

.model-select-input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--line, rgba(148, 163, 184, 0.3));
  border-radius: var(--radius-sm, 6px);
  background: var(--surface, #1a1a2e);
  color: var(--text, #e2e8f0);
  font-size: 0.92rem;
  box-sizing: border-box;
}

.model-select-input:focus {
  outline: none;
  border-color: rgba(96, 165, 250, 0.6);
}

.model-select-spinner {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  border: 2px solid rgba(148, 163, 184, 0.3);
  border-top-color: rgba(96, 165, 250, 0.8);
  border-radius: 50%;
  animation: model-select-spin 0.6s linear infinite;
}

@keyframes model-select-spin {
  to {
    transform: translateY(-50%) rotate(360deg);
  }
}

.model-select-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 100;
  max-height: 280px;
  overflow-y: auto;
  margin-top: 4px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius: var(--radius-sm, 6px);
  background: var(--surface, #1a1a2e);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}

.model-select-empty {
  padding: 12px;
  margin: 0;
  color: var(--text-soft, #94a3b8);
  font-size: 0.85rem;
  text-align: center;
}

.model-select-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--text, #e2e8f0);
  text-align: left;
  cursor: pointer;
  font-size: 0.85rem;
}

.model-select-option.is-highlighted {
  background: rgba(96, 165, 250, 0.15);
}

.model-select-option-id {
  font-weight: 600;
  font-family: monospace;
  font-size: 0.82rem;
  word-break: break-all;
}

.model-select-option-name {
  color: var(--text-soft, #94a3b8);
  font-size: 0.78rem;
}

.model-select-more {
  padding: 6px 12px;
  margin: 0;
  color: var(--text-soft, #94a3b8);
  font-size: 0.76rem;
  text-align: center;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
}
</style>
