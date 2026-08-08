<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { getActiveProvider, getProviderModels, getProviders } from '../api';
import { useI18n } from '../i18n';
import type { JobRunSettings, ProviderModel, ProviderStatus } from '../types';
import ModelSelect from './ModelSelect.vue';

const { t } = useI18n();

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
    ? t('advanced.modelSummary', {
      provider: selectedProvider.value?.name || provider.value,
      model: model.value || t('common.defaultModel'),
    })
    : t('advanced.autoModel');
  const custom = [
    temperature.value ? `T ${temperature.value}` : '',
    topP.value ? `topP ${topP.value}` : '',
    maxTokens.value ? t('advanced.maxTokensSummary', { value: maxTokens.value }) : '',
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
    loadError.value = error instanceof Error ? error.message : t('advanced.loadModelsError');
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
    loadError.value = error instanceof Error ? error.message : t('advanced.loadSettingsError');
    emitSettings();
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <details class="job-advanced-settings">
    <summary>
      <span>{{ t('advanced.title') }}</span>
      <strong>{{ loading ? t('common.loadingEllipsis') : summary }}</strong>
    </summary>
    <fieldset :disabled="props.busy || loading || modelLoading">
      <legend>{{ t('advanced.legend') }}</legend>
      <div class="advanced-settings-grid">
        <label>
          <span>{{ t('advanced.provider') }}</span>
          <select :value="provider" @change="onProviderChange">
            <option value="">{{ t('advanced.providerAuto') }}</option>
            <option v-for="item in configuredProviders" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
        </label>
        <label>
          <span>{{ t('advanced.model') }}</span>
          <ModelSelect
            :model-value="model"
            :models="models"
            :loading="modelLoading"
            :placeholder="t('advanced.modelPlaceholder')"
            @update:model-value="onModelChange"
          />
        </label>
        <label>
          <span>{{ t('advanced.temperature') }}</span>
          <input
            v-model="temperature"
            type="number"
            min="0"
            max="2"
            step="0.05"
            :placeholder="t('advanced.providerDefault')"
            @input="emitSettings"
          />
          <small>{{ t('advanced.temperatureHelp') }}</small>
        </label>
        <label>
          <span>{{ t('advanced.topP') }}</span>
          <input
            v-model="topP"
            type="number"
            min="0"
            max="1"
            step="0.05"
            :placeholder="t('advanced.providerDefault')"
            @input="emitSettings"
          />
          <small>{{ t('advanced.topPHelp') }}</small>
        </label>
        <label>
          <span>{{ t('advanced.maxOutput') }}</span>
          <input
            v-model="maxTokens"
            type="number"
            min="1"
            max="1000000"
            step="1"
            :placeholder="t('advanced.providerDefault')"
            @input="emitSettings"
          />
          <small>{{ t('advanced.maxOutputHelp') }}</small>
        </label>
      </div>
      <p v-if="loadError" class="settings-inline-error">{{ loadError }}</p>
      <p class="settings-note">{{ t('advanced.note') }}</p>
    </fieldset>
  </details>
</template>
