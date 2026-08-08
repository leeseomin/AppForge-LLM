<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { getActiveProvider, getProviderModels, getProviders } from '../api';
import type { JobRunSettings, ProviderModel, ProviderStatus } from '../types';
import ModelSelect from './ModelSelect.vue';

const props = defineProps<{
  settings: JobRunSettings;
  busy: boolean;
}>();

const emit = defineEmits<{
  'update:settings': [settings: JobRunSettings];
}>();

const providers = ref<ProviderStatus[]>([]);
const models = ref<ProviderModel[]>([]);
const provider = ref(props.settings.provider || '');
const model = ref(props.settings.model || '');
const temperature = ref<string | number>(props.settings.generation.temperature?.toString() || '');
const topP = ref<string | number>(props.settings.generation.topP?.toString() || '');
const maxTokens = ref<string | number>(props.settings.generation.maxTokens?.toString() || '');
const loading = ref(false);
const modelLoading = ref(false);
const loadError = ref('');

const configuredProviders = computed(() => providers.value.filter((item) => item.configured));
const selectedProvider = computed(() => providers.value.find((item) => item.id === provider.value));
const selectedModel = computed(() => models.value.find((item) => item.id === model.value));
const summary = computed(() => {
  const selection = provider.value
    ? `${selectedProvider.value?.name || provider.value} / ${model.value || '기본 모델'}`
    : '활성 모델 자동 선택';
  const custom = [
    temperature.value ? `T ${temperature.value}` : '',
    topP.value ? `topP ${topP.value}` : '',
    maxTokens.value ? `최대 ${maxTokens.value} tokens` : '',
  ].filter(Boolean).join(' · ');
  return custom ? `${selection} · ${custom}` : selection;
});

function optionalNumber(value: string | number): number | undefined {
  if (typeof value === 'string' && !value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function emitSettings(): void {
  const generation: JobRunSettings['generation'] = {};
  const parsedTemperature = optionalNumber(temperature.value);
  const parsedTopP = optionalNumber(topP.value);
  const parsedMaxTokens = optionalNumber(maxTokens.value);
  if (parsedTemperature !== undefined) generation.temperature = parsedTemperature;
  if (parsedTopP !== undefined) generation.topP = parsedTopP;
  if (parsedMaxTokens !== undefined) generation.maxTokens = Math.trunc(parsedMaxTokens);
  emit('update:settings', {
    provider: provider.value || null,
    model: model.value.trim() || null,
    generation,
    pricing: selectedModel.value?.cost || {},
  });
}

async function loadModels(providerId: string): Promise<void> {
  models.value = [];
  if (!providerId) return;
  modelLoading.value = true;
  loadError.value = '';
  try {
    const payload = await getProviderModels(providerId);
    models.value = payload.models || [];
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '모델 목록을 불러오지 못했습니다.';
  } finally {
    modelLoading.value = false;
  }
}

async function onProviderChange(event: Event): Promise<void> {
  provider.value = (event.target as HTMLSelectElement).value;
  model.value = '';
  await loadModels(provider.value);
  model.value = selectedProvider.value?.default_model || models.value[0]?.id || '';
  emitSettings();
}

function onModelChange(value: string): void {
  model.value = value;
  emitSettings();
}

watch(
  () => props.settings,
  (settings) => {
    const previousProvider = provider.value;
    provider.value = settings.provider || '';
    model.value = settings.model || '';
    temperature.value = settings.generation?.temperature ?? '';
    topP.value = settings.generation?.topP ?? '';
    maxTokens.value = settings.generation?.maxTokens ?? '';
    if (provider.value !== previousProvider && providers.value.length > 0) {
      void loadModels(provider.value);
    }
  },
  { deep: true },
);

onMounted(async () => {
  loading.value = true;
  loadError.value = '';
  try {
    const [providerPayload, active] = await Promise.all([getProviders(), getActiveProvider()]);
    providers.value = providerPayload.providers || [];
    provider.value = provider.value || active.provider || configuredProviders.value[0]?.id || '';
    await loadModels(provider.value);
    model.value = model.value
      || (provider.value === active.provider ? active.model || '' : '')
      || selectedProvider.value?.default_model
      || models.value[0]?.id
      || '';
    emitSettings();
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '모델 설정을 불러오지 못했습니다.';
    emitSettings();
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <details class="job-advanced-settings">
    <summary>
      <span>작업별 모델 · 고급 생성 설정</span>
      <strong>{{ loading ? '불러오는 중…' : summary }}</strong>
    </summary>
    <fieldset :disabled="props.busy || loading || modelLoading">
      <legend>작업별 LLM 설정</legend>
      <div class="advanced-settings-grid">
        <label>
          <span>프로바이더</span>
          <select :value="provider" @change="onProviderChange">
            <option value="">활성 프로바이더 자동 선택</option>
            <option v-for="item in configuredProviders" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </label>
        <label>
          <span>모델</span>
          <ModelSelect
            :model-value="model"
            :models="models"
            :loading="modelLoading"
            placeholder="모델 ID 또는 기본 모델"
            @update:model-value="onModelChange"
          />
        </label>
        <label>
          <span>Temperature</span>
          <input
            v-model="temperature"
            type="number"
            min="0"
            max="2"
            step="0.05"
            placeholder="프로바이더 기본값"
            @input="emitSettings"
          />
          <small>낮을수록 일관적, 높을수록 다양</small>
        </label>
        <label>
          <span>Top P</span>
          <input
            v-model="topP"
            type="number"
            min="0"
            max="1"
            step="0.05"
            placeholder="프로바이더 기본값"
            @input="emitSettings"
          />
          <small>누적 확률 기반 후보 범위</small>
        </label>
        <label>
          <span>최대 출력 토큰</span>
          <input
            v-model="maxTokens"
            type="number"
            min="1"
            max="1000000"
            step="1"
            placeholder="프로바이더 기본값"
            @input="emitSettings"
          />
          <small>각 LLM 호출의 출력 상한</small>
        </label>
      </div>
      <p v-if="loadError" class="settings-inline-error">{{ loadError }}</p>
      <p class="settings-note">이 선택은 전역 활성 모델을 바꾸지 않고 새 작업에만 고정됩니다.</p>
    </fieldset>
  </details>
</template>
