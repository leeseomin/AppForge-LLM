<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import ModelSelect from './ModelSelect.vue';
import {
  ApiError,
  deleteProvider,
  getActiveProvider,
  getProviderModels,
  getProviders,
  quickConnect,
  saveProvider,
  setActiveProvider,
  testProvider,
} from '../api';
import { useI18n } from '../i18n';
import type {
  ActiveSelection,
  ProviderModel,
  ProviderStatus,
  QuickConnectResult,
  TestResult,
} from '../types';

const { t } = useI18n();

const emit = defineEmits<{
  close: [];
  toast: [message: string];
  changed: [];
}>();

const providers = ref<ProviderStatus[]>([]);
const active = ref<ActiveSelection>({ provider: null, model: null });
const loading = ref(true);
const loadError = ref('');

const expandedId = ref<string | null>(null);
const busyId = ref<string | null>(null);
const showAdvanced = ref(false);

const draft = ref<Record<string, { apiKey: string; baseURL: string; defaultModel: string; useCustomModel: boolean }>>({});
const testResults = ref<Record<string, TestResult>>({});

const activeProviderId = ref<string>('');
const activeModel = ref<string>('');

// Quick connect state
const qcProvider = ref<string>('');
const qcApiKey = ref<string>('');
const qcModel = ref<string>('');
const qcBusy = ref(false);
const qcResult = ref<QuickConnectResult | null>(null);

const activeProvider = computed(() => providers.value.find((p) => p.id === activeProviderId.value) ?? null);

const qcProviderEntry = computed(() => providers.value.find((p) => p.id === qcProvider.value) ?? null);
const qcModelOptions = computed(() => modelCache.value.get(qcProvider.value) ?? []);
const qcModelLoading = computed(() => modelLoading.value.has(qcProvider.value));
const qcModelError = computed(() => modelLoadErrors.value[qcProvider.value] ?? '');
const qcModelPlaceholder = computed(() => {
  if (qcModelLoading.value) return t('modelSelect.loading');
  const first = qcModelOptions.value[0];
  return first ? `e.g. ${first.id}` : t('settings.modelId');
});
const qcNeedsBaseURL = computed(() => Boolean(qcProviderEntry.value?.base_url_required) && !qcProviderEntry.value?.base_url);
const qcCanSubmit = computed(
  () => Boolean(qcProvider.value) && qcApiKey.value.trim().length > 0 && (!qcNeedsBaseURL.value || qcBaseURL.value.trim().length > 0),
);

const activeModelOptions = computed(() => modelCache.value.get(activeProviderId.value) ?? []);
const activeModelLoading = computed(() => modelLoading.value.has(activeProviderId.value));
const activeModelError = computed(() => modelLoadErrors.value[activeProviderId.value] ?? '');

function setCachedModels(providerId: string, models: ProviderModel[]): void {
  modelCache.value = new Map(modelCache.value).set(providerId, models);
}

function setModelLoading(providerId: string, loading: boolean): void {
  const next = new Set(modelLoading.value);
  if (loading) {
    next.add(providerId);
  } else {
    next.delete(providerId);
  }
  modelLoading.value = next;
}

function setModelLoadError(providerId: string, message: string | null): void {
  const next = { ...modelLoadErrors.value };
  if (message) {
    next[providerId] = message;
  } else {
    delete next[providerId];
  }
  modelLoadErrors.value = next;
}

async function ensureModels(providerId: string): Promise<void> {
  if (!providerId || modelCache.value.has(providerId) || modelLoading.value.has(providerId)) return;
  const provider = providers.value.find((p) => p.id === providerId);
  if (provider?.models?.length) {
    setCachedModels(providerId, provider.models);
    return;
  }
  setModelLoading(providerId, true);
  setModelLoadError(providerId, null);
  try {
    const payload = await getProviderModels(providerId);
    setCachedModels(providerId, payload.models ?? []);
  } catch {
    setCachedModels(providerId, []);
    setModelLoadError(providerId, t('settings.modelLoadError'));
  } finally {
    setModelLoading(providerId, false);
  }
}

watch(activeProviderId, (id) => {
  if (id) ensureModels(id);
});
const qcBaseURL = ref<string>('');

// Lazy-loaded model cache (F-2 A: memory only, cleared when modal closes)
const modelCache = ref<Map<string, ProviderModel[]>>(new Map());
const modelLoading = ref<Set<string>>(new Set());
const modelLoadErrors = ref<Record<string, string>>({});
const expandedChips = ref<Set<string>>(new Set());
const MODEL_CHIPS_LIMIT = 10;

onMounted(async () => {
  await reload();
});

async function reload() {
  loading.value = true;
  loadError.value = '';
  try {
    const [providersPayload, activePayload] = await Promise.all([
      getProviders(),
      getActiveProvider(),
    ]);
    providers.value = providersPayload.providers;
    active.value = activePayload;
    activeProviderId.value = activePayload.provider ?? providers.value[0]?.id ?? '';
    activeModel.value = activePayload.model ?? '';
    if (!qcProvider.value) qcProvider.value = activePayload.provider ?? providers.value[0]?.id ?? '';
    qcBaseURL.value = qcProviderEntry.value?.base_url ?? '';
  } catch (error) {
    loadError.value = readableError(error, t('settings.providerLoadError'));
  } finally {
    loading.value = false;
  }
}

function onQcProviderChange() {
  qcModel.value = '';
  qcResult.value = null;
  qcBaseURL.value = qcProviderEntry.value?.base_url ?? '';
  if (qcProvider.value) ensureModels(qcProvider.value);
}

async function runQuickConnect() {
  if (!qcCanSubmit.value) return;
  qcBusy.value = true;
  qcResult.value = null;
  try {
    const result = await quickConnect({
      provider: qcProvider.value,
      apiKey: qcApiKey.value.trim(),
      baseURL: qcNeedsBaseURL.value ? qcBaseURL.value.trim() : qcBaseURL.value.trim() || undefined,
      model: qcModel.value.trim() || undefined,
    });
    qcResult.value = result;
    if (result.ok) {
      qcApiKey.value = '';
      emit('toast', t('settings.quickConnectedToast', { provider: result.provider, model: result.model || t('common.defaultModel') }));
      emit('changed');
      await reload();
    } else {
      const stepLabel = quickConnectStepLabel(result.step);
      emit('toast', t('settings.quickFailureToast', { step: stepLabel, error: result.error || t('settings.unknownError') }));
    }
  } catch (error) {
    const message = readableError(error, t('settings.quickError'));
    qcResult.value = { ok: false, step: 'save', error: message, provider: qcProvider.value };
    emit('toast', message);
  } finally {
    qcBusy.value = false;
  }
}

function ensureDraft(provider: ProviderStatus) {
  if (!draft.value[provider.id]) {
    draft.value[provider.id] = {
      apiKey: '',
      baseURL: provider.base_url ?? '',
      defaultModel: provider.default_model ?? '',
      useCustomModel: Boolean(provider.default_model) && !(provider.models ?? []).some((m) => m.id === provider.default_model),
    };
  }
  return draft.value[provider.id];
}

function toggle(provider: ProviderStatus) {
  ensureDraft(provider);
  const nextId = expandedId.value === provider.id ? null : provider.id;
  expandedId.value = nextId;
  if (nextId) ensureModels(provider.id);
}

function keyPlaceholder(provider: ProviderStatus) {
  if (provider.has_key) {
    return provider.key_source === 'env'
      ? t('settings.envInUse', { key: provider.env_key ?? '' })
      : t('settings.keyStored');
  }
  return t('settings.keyInput');
}

function statusLabel(provider: ProviderStatus) {
  if (active.value.provider === provider.id) return t('common.active');
  if (!provider.has_key) return t('settings.keyRequired');
  if (!provider.configured) return t('settings.setupRequired');
  return t('common.available');
}

function quickConnectStepLabel(step: string) {
  if (step === 'save') return t('settings.stepSave');
  if (step === 'test') return t('settings.stepTest');
  if (step === 'activate') return t('settings.stepActivate');
  return step;
}

function statusClass(provider: ProviderStatus) {
  if (active.value.provider === provider.id) return 'is-active';
  if (!provider.has_key || !provider.configured) return 'is-warn';
  return 'is-ready';
}

function modelSuggestions(provider: ProviderStatus): string[] {
  const models = providerModelOptions(provider);
  if (expandedChips.value.has(provider.id)) return models.map((m) => m.id);
  return models.slice(0, MODEL_CHIPS_LIMIT).map((m) => m.id);
}

function modelChipExtraCount(provider: ProviderStatus): number {
  const models = providerModelOptions(provider);
  return Math.max(0, models.length - MODEL_CHIPS_LIMIT);
}

function toggleChips(provider: ProviderStatus): void {
  const next = new Set(expandedChips.value);
  if (next.has(provider.id)) {
    next.delete(provider.id);
  } else {
    next.add(provider.id);
    ensureModels(provider.id);
  }
  expandedChips.value = next;
}

function ensureProviderModels(provider: ProviderStatus): void {
  ensureModels(provider.id);
}

function providerModelOptions(provider: ProviderStatus): ProviderModel[] {
  return modelCache.value.get(provider.id) ?? provider.models ?? [];
}

function providerModelLoadError(provider: ProviderStatus): string {
  return modelLoadErrors.value[provider.id] ?? '';
}

async function save(provider: ProviderStatus) {
  const d = ensureDraft(provider);
  busyId.value = provider.id;
  try {
    const payload: { apiKey?: string; baseURL?: string; defaultModel?: string } = {};
    if (d.apiKey.trim()) payload.apiKey = d.apiKey.trim();
    if (provider.kind === 'openai-compatible' || d.baseURL.trim()) payload.baseURL = d.baseURL.trim();
    if (d.defaultModel.trim()) payload.defaultModel = d.defaultModel.trim();
    const result = await saveProvider(provider.id, payload);
    const index = providers.value.findIndex((p) => p.id === provider.id);
    if (index >= 0) providers.value[index] = result.status;
    d.apiKey = '';
    emit('toast', t('settings.savedToast', { provider: provider.name }));
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, t('settings.saveError')));
  } finally {
    busyId.value = null;
  }
}

async function remove(provider: ProviderStatus) {
  if (!provider.has_key || provider.key_source !== 'stored') {
    emit('toast', t('settings.noSavedKey'));
    return;
  }
  busyId.value = provider.id;
  try {
    await deleteProvider(provider.id);
    const index = providers.value.findIndex((p) => p.id === provider.id);
    if (index >= 0) {
      providers.value[index] = {
        ...providers.value[index],
        has_key: false,
        key_source: 'none',
        configured: false,
        default_model: null,
      };
    }
    draft.value[provider.id] = { ...ensureDraft(provider), apiKey: '', defaultModel: '' };
    emit('toast', t('settings.deletedToast', { provider: provider.name }));
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, t('settings.deleteError')));
  } finally {
    busyId.value = null;
  }
}

async function test(provider: ProviderStatus) {
  const d = ensureDraft(provider);
  busyId.value = provider.id;
  testResults.value[provider.id] = { ok: false, error: t('settings.connectingStatus') };
  try {
    const body: { apiKey?: string; baseURL?: string; model?: string } = {};
    if (d.apiKey.trim()) body.apiKey = d.apiKey.trim();
    if (provider.kind === 'openai-compatible' || d.baseURL.trim()) body.baseURL = d.baseURL.trim();
    if (d.defaultModel.trim()) body.model = d.defaultModel.trim();
    const result = await testProvider(provider.id, body);
    testResults.value[provider.id] = result;
    emit('toast', result.ok
      ? t('settings.providerConnectionSuccess', { provider: provider.name })
      : t('settings.providerConnectionFailed', { provider: provider.name }));
  } catch (error) {
    testResults.value[provider.id] = { ok: false, error: readableError(error, t('settings.connectionTestError')) };
    emit('toast', t('settings.connectionTestFailed'));
  } finally {
    busyId.value = null;
  }
}

async function activate(provider: ProviderStatus) {
  const d = ensureDraft(provider);
  const model = d.defaultModel.trim() || provider.default_model || providerModelOptions(provider)[0]?.id || '';
  busyId.value = provider.id;
  try {
    const result = await setActiveProvider(provider.id, model || null);
    active.value = result;
    activeProviderId.value = provider.id;
    activeModel.value = result.model ?? model;
    emit('toast', t('settings.activatedToast', { provider: provider.name, model: model ? ` / ${model} ` : ' ' }));
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, t('settings.activateError')));
  } finally {
    busyId.value = null;
  }
}

async function applyActiveModel() {
  busyId.value = '__active__';
  try {
    const provider = activeProviderId.value || null;
    const model = activeModel.value || null;
    const result = await setActiveProvider(provider, model);
    active.value = result;
    emit('toast', t('settings.modelApplied'));
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, t('settings.modelApplyError')));
  } finally {
    busyId.value = null;
  }
}

function readableError(error: unknown, fallback: string) {
  if (error instanceof ApiError) return error.payload.message || error.payload.title || fallback;
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}
</script>

<template>
  <div class="settings-overlay" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
    <div class="settings-card">
      <header class="settings-header">
        <div>
          <p class="section-kicker">{{ t('settings.kicker') }}</p>
          <h2 id="settingsTitle">{{ t('settings.title') }}</h2>
        </div>
        <button class="settings-close" type="button" :aria-label="t('settings.closeLabel')" @click="emit('close')">×</button>
      </header>

      <p v-if="loading" class="settings-empty">{{ t('settings.loading') }}</p>
      <p v-else-if="loadError" class="settings-error">{{ loadError }}</p>

      <section v-if="!loading && !loadError" class="quick-connect">
        <h3>{{ t('settings.quickTitle') }}</h3>
        <p class="settings-hint">{{ t('settings.quickHelp') }}</p>
        <div class="qc-row">
          <label class="qc-field qc-provider">
            <span>{{ t('settings.provider') }}</span>
            <select v-model="qcProvider" @change="onQcProviderChange">
              <option value="" disabled>{{ t('settings.choose') }}</option>
              <option v-for="p in providers" :key="p.id" :value="p.id">
                {{ p.name }} ({{ p.id }})
              </option>
            </select>
          </label>
          <label class="qc-field qc-key">
            <span>{{ t('settings.apiKey') }}</span>
            <input
              v-model="qcApiKey"
              type="password"
              :placeholder="qcProviderEntry ? (qcProviderEntry.has_key ? t('settings.keyStored') : t('settings.keyInput')) : t('settings.chooseProviderFirst')"
              autocomplete="off"
            />
          </label>
        </div>
        <div class="qc-row">
          <label v-if="qcNeedsBaseURL" class="qc-field qc-base">
            <span>{{ t('settings.baseUrl') }} <small>({{ t('common.required') }})</small></span>
            <input v-model="qcBaseURL" type="text" :placeholder="qcProviderEntry?.base_url_default || 'https://...'" />
          </label>
          <label class="qc-field qc-model">
            <span>{{ t('settings.model') }} <small>({{ t('common.optional') }})</small></span>
            <ModelSelect
              v-model="qcModel"
              :models="qcModelOptions"
              :loading="qcModelLoading"
              :placeholder="qcModelPlaceholder"
            />
            <small v-if="qcModelError" class="field-warning">{{ qcModelError }}</small>
          </label>
        </div>
        <p v-if="qcProviderEntry?.env_key" class="form-hint">
          {{ t('settings.envHint', { key: qcProviderEntry.env_key }) }}
        </p>
        <p v-if="qcProviderEntry?.docs_url" class="form-hint">
          <a :href="qcProviderEntry.docs_url" target="_blank" rel="noopener noreferrer">{{ t('settings.apiGuide') }}</a>
        </p>
        <div class="qc-actions">
          <button
            class="primary-button"
            type="button"
            :disabled="!qcCanSubmit || qcBusy"
            @click="runQuickConnect"
          >
            {{ qcBusy ? t('settings.connecting') : t('settings.connectActivate') }}
          </button>
        </div>
        <p v-if="qcResult" class="test-result" :class="{ ok: qcResult.ok }">
          <template v-if="qcResult.ok">
            ✓ {{ t('settings.connectionSuccess', { provider: qcResult.provider, model: qcResult.model || t('common.defaultModel') }) }}
            <span v-if="qcResult.test?.text"> — {{ t('settings.response', { text: qcResult.test.text }) }}</span>
          </template>
          <template v-else>
            ✕ {{ t('settings.failure', { step: quickConnectStepLabel(qcResult.step), error: qcResult.error || t('settings.unknownError') }) }}
          </template>
        </p>
      </section>

      <section v-if="!loading && !loadError" class="active-block">
        <h3>{{ t('settings.activeTitle') }}</h3>
        <p class="settings-hint">{{ t('settings.activeHelp') }}</p>
        <div class="active-row">
          <label class="active-field">
            <span>{{ t('settings.provider') }}</span>
            <select v-model="activeProviderId">
              <option value="" disabled>{{ t('settings.choose') }}</option>
              <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </label>
          <label class="active-field">
            <span>{{ t('settings.model') }}</span>
            <ModelSelect
              v-model="activeModel"
              :models="activeModelOptions"
              :loading="activeModelLoading"
              :placeholder="t('settings.modelExample')"
            />
            <small v-if="activeModelError" class="field-warning">{{ activeModelError }}</small>
          </label>
          <button
            class="secondary-button"
            type="button"
            :disabled="busyId === '__active__'"
            @click="applyActiveModel"
          >
            {{ t('common.apply') }}
          </button>
        </div>
        <p v-if="active.provider" class="active-current">
          {{ t('settings.current', { provider: active.provider, model: active.model || t('common.defaultModel') }) }}
        </p>
      </section>

      <section v-if="!loading && !loadError" class="advanced-block">
        <button class="advanced-toggle" type="button" @click="showAdvanced = !showAdvanced">
          <span class="provider-chevron" aria-hidden="true">{{ showAdvanced ? '▾' : '▸' }}</span>
          {{ t('settings.advanced') }}
        </button>

        <div v-if="showAdvanced" class="provider-list">
          <article
            v-for="provider in providers"
            :key="provider.id"
            class="provider-row"
            :class="{ 'is-expanded': expandedId === provider.id, 'is-active-provider': active.provider === provider.id }"
          >
            <button class="provider-summary" type="button" @click="toggle(provider)">
              <span class="provider-name">{{ provider.name }}</span>
              <span class="provider-key-hint">
                {{ provider.has_key ? (provider.key_source === 'env' ? t('settings.envSource') : t('settings.keySaved')) : t('settings.noKey') }}
              </span>
              <span class="provider-status" :class="statusClass(provider)">{{ statusLabel(provider) }}</span>
              <span class="provider-chevron" aria-hidden="true">{{ expandedId === provider.id ? '▾' : '▸' }}</span>
            </button>

            <div v-if="expandedId === provider.id" class="provider-body">
              <label class="form-field">
                <span>{{ t('settings.apiKey') }}</span>
                <input
                  v-model="ensureDraft(provider).apiKey"
                  :type="'password'"
                  :placeholder="keyPlaceholder(provider)"
                  autocomplete="off"
                />
              </label>
              <p v-if="provider.env_key" class="form-hint">
                {{ t('settings.envHint', { key: provider.env_key }) }}
              </p>

              <label class="form-field">
                <span>{{ t('settings.baseUrl') }} <small v-if="provider.base_url_required">({{ t('common.required') }})</small></span>
                <input
                  v-model="ensureDraft(provider).baseURL"
                  :type="'text'"
                  :placeholder="provider.base_url_default || 'https://...'"
                />
              </label>

              <label class="form-field">
                <span>{{ t('settings.defaultModel') }}</span>
                <ModelSelect
                  v-model="ensureDraft(provider).defaultModel"
                  :models="providerModelOptions(provider)"
                  :loading="modelLoading.has(provider.id)"
                  :placeholder="t('settings.modelId')"
                  @update:model-value="ensureProviderModels(provider)"
                />
                <small v-if="providerModelLoadError(provider)" class="field-warning">
                  {{ providerModelLoadError(provider) }}
                </small>
              </label>
              <div class="model-chips" :class="{ 'is-expanded': expandedChips.has(provider.id) }">
                <button
                  v-for="id in modelSuggestions(provider)"
                  :key="id"
                  type="button"
                  class="model-chip"
                  @click="ensureDraft(provider).defaultModel = id"
                >
                  {{ id }}
                </button>
                <button
                  v-if="modelChipExtraCount(provider) > 0 && !expandedChips.has(provider.id)"
                  type="button"
                  class="model-chip model-chip-more"
                  @click="toggleChips(provider)"
                >
                  {{ t('settings.showMore', { count: modelChipExtraCount(provider) }) }}
                </button>
                <button
                  v-if="expandedChips.has(provider.id)"
                  type="button"
                  class="model-chip model-chip-more"
                  @click="toggleChips(provider)"
                >
                  {{ t('settings.collapse') }}
                </button>
              </div>

              <p v-if="provider.docs_url" class="form-hint">
                <a :href="provider.docs_url" target="_blank" rel="noopener noreferrer">{{ t('settings.apiGuide') }}</a>
              </p>

              <p v-if="testResults[provider.id]" class="test-result" :class="{ ok: testResults[provider.id].ok }">
                {{ testResults[provider.id].ok ? `✓ ${t('settings.connectionOk')}` : `✕ ${testResults[provider.id].error || t('settings.connectionFailed')}` }}
                <span v-if="testResults[provider.id].ok && testResults[provider.id].text">
                  — {{ t('settings.response', { text: testResults[provider.id].text || '' }) }}
                </span>
              </p>

              <div class="provider-actions">
                <button class="secondary-button" type="button" :disabled="busyId === provider.id" @click="save(provider)">
                  {{ t('common.save') }}
                </button>
                <button class="ghost-button" type="button" :disabled="busyId === provider.id" @click="test(provider)">
                  {{ t('settings.testConnection') }}
                </button>
                <button class="primary-button" type="button" :disabled="busyId === provider.id" @click="activate(provider)">
                  {{ t('settings.activateModel') }}
                </button>
                <button
                  class="ghost-button danger"
                  type="button"
                  :disabled="busyId === provider.id"
                  @click="remove(provider)"
                >
                  {{ t('settings.deleteKey') }}
                </button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <footer class="settings-footer">
        <p class="settings-hint">{{ t('settings.securityNote') }}</p>
        <button class="secondary-button" type="button" @click="emit('close')">{{ t('common.close') }}</button>
      </footer>
    </div>
  </div>
</template>
