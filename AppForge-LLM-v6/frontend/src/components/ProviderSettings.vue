<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import ModelSelect from './ModelSelect.vue';
import {
  ApiError,
  deleteProvider,
  getActiveProvider,
  getOAuthProviders,
  getProviderModels,
  getProviders,
  pollOAuth,
  quickConnect,
  refreshOAuth,
  saveProvider,
  setActiveProvider,
  startOAuth,
  testProvider,
} from '../api';
import type {
  ActiveSelection,
  OAuthProvider,
  OAuthPollResult,
  ProviderModel,
  ProviderStatus,
  QuickConnectResult,
  TestResult,
} from '../types';

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
  if (qcModelLoading.value) return '모델 목록 로딩 중...';
  const first = qcModelOptions.value[0];
  return first ? `예: ${first.id}` : '모델 ID';
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
    setModelLoadError(providerId, '모델 목록 로딩 실패. 모델 ID를 직접 입력할 수 있습니다.');
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

// OAuth state
const oauthProviders = ref<OAuthProvider[]>([]);
const oauthProviderId = ref<string>('');
const oauthMethod = ref<string>('browser');
const oauthBusy = ref(false);
const oauthResult = ref<OAuthPollResult | null>(null);
const oauthInstructions = ref<string>('');
const oauthUrl = ref<string>('');
let oauthPollTimer: ReturnType<typeof setInterval> | null = null;

const oauthProviderEntry = computed(() => oauthProviders.value.find((p) => p.id === oauthProviderId.value) ?? null);
const oauthMethodOptions = computed(() => oauthProviderEntry.value?.methods ?? []);

onMounted(async () => {
  await reload();
});

async function reload() {
  loading.value = true;
  loadError.value = '';
  try {
    const [providersPayload, activePayload, oauthPayload] = await Promise.all([
      getProviders(),
      getActiveProvider(),
      getOAuthProviders().catch(() => ({ providers: [] })),
    ]);
    providers.value = providersPayload.providers;
    active.value = activePayload;
    activeProviderId.value = activePayload.provider ?? providers.value[0]?.id ?? '';
    activeModel.value = activePayload.model ?? '';
    if (!qcProvider.value) qcProvider.value = activePayload.provider ?? providers.value[0]?.id ?? '';
    qcBaseURL.value = qcProviderEntry.value?.base_url ?? '';
    oauthProviders.value = oauthPayload.providers;
    if (!oauthProviderId.value && oauthProviders.value.length > 0) {
      oauthProviderId.value = oauthProviders.value[0].id;
      oauthMethod.value = oauthProviders.value[0].methods[0]?.id ?? 'browser';
    }
  } catch (error) {
    loadError.value = readableError(error, '프로바이더 목록을 불러오지 못했습니다.');
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

function onOauthProviderChange() {
  oauthResult.value = null;
  oauthInstructions.value = '';
  oauthUrl.value = '';
  stopOAuthPoll();
  const methods = oauthProviderEntry.value?.methods ?? [];
  oauthMethod.value = methods[0]?.id ?? 'browser';
}

function stopOAuthPoll() {
  if (oauthPollTimer) {
    clearInterval(oauthPollTimer);
    oauthPollTimer = null;
  }
}

async function runOAuthLogin() {
  if (!oauthProviderId.value) return;
  oauthBusy.value = true;
  oauthResult.value = null;
  oauthInstructions.value = '';
  oauthUrl.value = '';
  stopOAuthPoll();
  try {
    const result = await startOAuth({
      provider: oauthProviderId.value,
      method: oauthMethod.value,
    });
    oauthInstructions.value = result.instructions;
    oauthUrl.value = result.url;
    if (oauthMethod.value === 'browser' && result.url) {
      window.open(result.url, '_blank', 'noopener,noreferrer');
    }
    const providerId = oauthProviderId.value;
    const pollId = result.pollId;
    oauthPollTimer = setInterval(async () => {
      try {
        const pollResult = await pollOAuth(providerId, pollId);
        if (pollResult.status === 'success') {
          stopOAuthPoll();
          oauthResult.value = pollResult;
          oauthBusy.value = false;
          emit('toast', `${providerId} OAuth 로그인 성공`);
          emit('changed');
          await reload();
        } else if (pollResult.status === 'failed') {
          stopOAuthPoll();
          oauthResult.value = pollResult;
          oauthBusy.value = false;
          emit('toast', `OAuth 로그인 실패: ${pollResult.error || '오류'}`);
        }
      } catch {
        // keep polling on transient errors
      }
    }, 2000);
  } catch (error) {
    const message = readableError(error, 'OAuth 시작에 실패했습니다.');
    oauthResult.value = { status: 'failed', error: message };
    oauthBusy.value = false;
    emit('toast', message);
  }
}

async function doRefreshOAuth() {
  if (!oauthProviderId.value) return;
  try {
    await refreshOAuth(oauthProviderId.value);
    emit('toast', `${oauthProviderId.value} 토큰 갱신됨`);
  } catch (error) {
    emit('toast', readableError(error, '토큰 갱신 실패'));
  }
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
      emit('toast', `${result.provider} / ${result.model} 연결 및 활성화 완료`);
      emit('changed');
      await reload();
    } else {
      const stepLabel = { save: '저장', test: '연결 테스트', activate: '활성화' }[result.step] || result.step;
      emit('toast', `빠른 연결 실패 (${stepLabel}): ${result.error || '오류'}`);
    }
  } catch (error) {
    const message = readableError(error, '빠른 연결에 실패했습니다.');
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
    return provider.key_source === 'env' ? `${provider.env_key ?? ''} 환경변수 사용 중` : '저장된 키 사용 중 (덮어쓰려면 입력)';
  }
  return 'API 키 입력';
}

function statusLabel(provider: ProviderStatus) {
  if (active.value.provider === provider.id) return '활성';
  if (!provider.has_key) return '키 필요';
  if (!provider.configured) return '설정 필요';
  return '사용 가능';
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
    emit('toast', `${provider.name} 설정을 저장했습니다.`);
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, '저장에 실패했습니다.'));
  } finally {
    busyId.value = null;
  }
}

async function remove(provider: ProviderStatus) {
  if (!provider.has_key || provider.key_source !== 'stored') {
    emit('toast', '저장된 키가 없습니다.');
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
    emit('toast', `${provider.name} 설정을 삭제했습니다.`);
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, '삭제에 실패했습니다.'));
  } finally {
    busyId.value = null;
  }
}

async function test(provider: ProviderStatus) {
  const d = ensureDraft(provider);
  busyId.value = provider.id;
  testResults.value[provider.id] = { ok: false, error: '연결을 시도하는 중...' };
  try {
    const body: { apiKey?: string; baseURL?: string; model?: string } = {};
    if (d.apiKey.trim()) body.apiKey = d.apiKey.trim();
    if (provider.kind === 'openai-compatible' || d.baseURL.trim()) body.baseURL = d.baseURL.trim();
    if (d.defaultModel.trim()) body.model = d.defaultModel.trim();
    const result = await testProvider(provider.id, body);
    testResults.value[provider.id] = result;
    emit('toast', result.ok ? `${provider.name} 연결 성공` : `${provider.name} 연결 실패`);
  } catch (error) {
    testResults.value[provider.id] = { ok: false, error: readableError(error, '연결 테스트에 실패했습니다.') };
    emit('toast', '연결 테스트 실패');
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
    emit('toast', `${provider.name}${model ? ` / ${model}` : ''} 을(를) 활성 모델로 설정했습니다.`);
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, '활성 모델 설정에 실패했습니다.'));
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
    emit('toast', '사용할 모델을 적용했습니다.');
    emit('changed');
  } catch (error) {
    emit('toast', readableError(error, '모델 적용에 실패했습니다.'));
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
          <p class="section-kicker">EXTERNAL LLM · MULTI-PROVIDER</p>
          <h2 id="settingsTitle">LLM 연결 설정</h2>
        </div>
        <button class="settings-close" type="button" aria-label="닫기" @click="emit('close')">×</button>
      </header>

      <p v-if="loading" class="settings-empty">프로바이더 목록을 불러오는 중...</p>
      <p v-else-if="loadError" class="settings-error">{{ loadError }}</p>

      <section v-if="!loading && !loadError" class="quick-connect">
        <h3>빠른 연결</h3>
        <p class="settings-hint">
          프로바이더와 API 키만 입력하면 저장 · 연결 테스트 · 활성 모델 설정까지 한 번에 처리됩니다.
          모델은 비워두면 프로바이더 기본값을 사용합니다.
        </p>
        <div class="qc-row">
          <label class="qc-field qc-provider">
            <span>프로바이더</span>
            <select v-model="qcProvider" @change="onQcProviderChange">
              <option value="" disabled>선택하세요</option>
              <option v-for="p in providers" :key="p.id" :value="p.id">
                {{ p.name }} ({{ p.id }})
              </option>
            </select>
          </label>
          <label class="qc-field qc-key">
            <span>API 키</span>
            <input
              v-model="qcApiKey"
              type="password"
              :placeholder="qcProviderEntry ? (qcProviderEntry.has_key ? '저장된 키 사용 중 (덮어쓰려면 입력)' : 'API 키 입력') : '프로바이더 먼저 선택'"
              autocomplete="off"
            />
          </label>
        </div>
        <div class="qc-row">
          <label v-if="qcNeedsBaseURL" class="qc-field qc-base">
            <span>Base URL <small>(필수)</small></span>
            <input v-model="qcBaseURL" type="text" :placeholder="qcProviderEntry?.base_url_default || 'https://...'" />
          </label>
          <label class="qc-field qc-model">
            <span>모델 <small>(선택)</small></span>
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
          환경변수 <code>{{ qcProviderEntry.env_key }}</code> 가 설정되어 있으면 자동으로 사용합니다.
        </p>
        <p v-if="qcProviderEntry?.docs_url" class="form-hint">
          <a :href="qcProviderEntry.docs_url" target="_blank" rel="noopener noreferrer">API 키 발급 가이드 ↗</a>
        </p>
        <div class="qc-actions">
          <button
            class="primary-button"
            type="button"
            :disabled="!qcCanSubmit || qcBusy"
            @click="runQuickConnect"
          >
            {{ qcBusy ? '연결 중...' : '연결하고 활성화' }}
          </button>
        </div>
        <p v-if="qcResult" class="test-result" :class="{ ok: qcResult.ok }">
          <template v-if="qcResult.ok">
            ✓ 연결 성공 — 활성: <strong>{{ qcResult.provider }}</strong> / <strong>{{ qcResult.model }}</strong>
            <span v-if="qcResult.test?.text"> — 응답: “{{ qcResult.test.text }}”</span>
          </template>
          <template v-else>
            ✕ 실패 ({{ { save: '저장', test: '연결 테스트', activate: '활성화' }[qcResult.step] || qcResult.step }}): {{ qcResult.error }}
          </template>
        </p>
      </section>

      <section v-if="!loading && !loadError && oauthProviders.length > 0" class="oauth-block">
        <h3>OAuth 로그인</h3>
        <p class="settings-hint">
          ChatGPT Plus/Pro, xAI Grok 구독, GitHub Copilot으로 브라우저 또는 장치 코드로 로그인합니다.
          API 키 없이 구독을 통해 접근할 수 있습니다.
        </p>
        <div class="qc-row">
          <label class="qc-field">
            <span>프로바이더</span>
            <select v-model="oauthProviderId" @change="onOauthProviderChange">
              <option v-for="p in oauthProviders" :key="p.id" :value="p.id">
                {{ p.name || p.id }}
              </option>
            </select>
          </label>
          <label class="qc-field">
            <span>방식</span>
            <select v-model="oauthMethod">
              <option v-for="m in oauthMethodOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </label>
        </div>
        <div class="qc-actions">
          <button
            class="primary-button"
            type="button"
            :disabled="!oauthProviderId || oauthBusy"
            @click="runOAuthLogin"
          >
            {{ oauthBusy ? '인증 대기 중...' : 'OAuth 로그인' }}
          </button>
          <button
            class="ghost-button"
            type="button"
            :disabled="!oauthProviderId || oauthBusy"
            @click="doRefreshOAuth"
          >
            토큰 갱신
          </button>
        </div>
        <p v-if="oauthInstructions" class="form-hint">{{ oauthInstructions }}</p>
        <p v-if="oauthUrl" class="form-hint">
          <a :href="oauthUrl" target="_blank" rel="noopener noreferrer">인증 페이지 열기 ↗</a>
        </p>
        <p v-if="oauthResult" class="test-result" :class="{ ok: oauthResult.status === 'success' }">
          <template v-if="oauthResult.status === 'success'">
            ✓ OAuth 인증 성공 — <strong>{{ oauthResult.provider }}</strong>
          </template>
          <template v-else-if="oauthResult.status === 'failed'">
            ✕ 실패: {{ oauthResult.error }}
          </template>
        </p>
      </section>

      <section v-if="!loading && !loadError" class="active-block">
        <h3>사용할 모델</h3>
        <p class="settings-hint">
          AppForge 파이프라인이 호출할 외부 LLM 프로바이더와 모델입니다. 저장 후 바로 실행에 적용됩니다.
        </p>
        <div class="active-row">
          <label class="active-field">
            <span>프로바이더</span>
            <select v-model="activeProviderId">
              <option value="" disabled>선택하세요</option>
              <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
          </label>
          <label class="active-field">
            <span>모델</span>
            <ModelSelect
              v-model="activeModel"
              :models="activeModelOptions"
              :loading="activeModelLoading"
              placeholder="예: gpt-4o-mini"
            />
            <small v-if="activeModelError" class="field-warning">{{ activeModelError }}</small>
          </label>
          <button
            class="secondary-button"
            type="button"
            :disabled="busyId === '__active__'"
            @click="applyActiveModel"
          >
            적용
          </button>
        </div>
        <p v-if="active.provider" class="active-current">
          현재 활성: <strong>{{ active.provider }}</strong> / <strong>{{ active.model || '기본 모델' }}</strong>
        </p>
      </section>

      <section v-if="!loading && !loadError" class="advanced-block">
        <button class="advanced-toggle" type="button" @click="showAdvanced = !showAdvanced">
          <span class="provider-chevron" aria-hidden="true">{{ showAdvanced ? '▾' : '▸' }}</span>
          고급 설정 (프로바이더별 상세 편집)
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
                {{ provider.has_key ? (provider.key_source === 'env' ? '환경변수' : '키 저장됨') : '키 없음' }}
              </span>
              <span class="provider-status" :class="statusClass(provider)">{{ statusLabel(provider) }}</span>
              <span class="provider-chevron" aria-hidden="true">{{ expandedId === provider.id ? '▾' : '▸' }}</span>
            </button>

            <div v-if="expandedId === provider.id" class="provider-body">
              <label class="form-field">
                <span>API 키</span>
                <input
                  v-model="ensureDraft(provider).apiKey"
                  :type="'password'"
                  :placeholder="keyPlaceholder(provider)"
                  autocomplete="off"
                />
              </label>
              <p v-if="provider.env_key" class="form-hint">
                환경변수 <code>{{ provider.env_key }}</code> 가 설정되어 있으면 자동으로 사용합니다.
              </p>

              <label class="form-field">
                <span>Base URL <small v-if="provider.base_url_required">(필수)</small></span>
                <input
                  v-model="ensureDraft(provider).baseURL"
                  :type="'text'"
                  :placeholder="provider.base_url_default || 'https://...'"
                />
              </label>

              <label class="form-field">
                <span>기본 모델</span>
                <ModelSelect
                  v-model="ensureDraft(provider).defaultModel"
                  :models="providerModelOptions(provider)"
                  :loading="modelLoading.has(provider.id)"
                  placeholder="모델 ID"
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
                  더 보기 ({{ modelChipExtraCount(provider) }}개)
                </button>
                <button
                  v-if="expandedChips.has(provider.id)"
                  type="button"
                  class="model-chip model-chip-more"
                  @click="toggleChips(provider)"
                >
                  접기
                </button>
              </div>

              <p v-if="provider.docs_url" class="form-hint">
                <a :href="provider.docs_url" target="_blank" rel="noopener noreferrer">API 키 발급 가이드 ↗</a>
              </p>

              <p v-if="testResults[provider.id]" class="test-result" :class="{ ok: testResults[provider.id].ok }">
                {{ testResults[provider.id].ok ? '✓ 연결 성공' : '✕ ' + (testResults[provider.id].error || '연결 실패') }}
                <span v-if="testResults[provider.id].ok && testResults[provider.id].text">
                  — 응답: “{{ testResults[provider.id].text }}”
                </span>
              </p>

              <div class="provider-actions">
                <button class="secondary-button" type="button" :disabled="busyId === provider.id" @click="save(provider)">
                  저장
                </button>
                <button class="ghost-button" type="button" :disabled="busyId === provider.id" @click="test(provider)">
                  연결 테스트
                </button>
                <button class="primary-button" type="button" :disabled="busyId === provider.id" @click="activate(provider)">
                  이 모델 활성화
                </button>
                <button
                  class="ghost-button danger"
                  type="button"
                  :disabled="busyId === provider.id"
                  @click="remove(provider)"
                >
                  키 삭제
                </button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <footer class="settings-footer">
        <p class="settings-hint">
          API 키는 로컬 브릿지 서버(<code>llm_bridge</code>)의 설정 파일에만 저장되며, 이 화면에 다시 표시되지
          않습니다.
        </p>
        <button class="secondary-button" type="button" @click="emit('close')">닫기</button>
      </footer>
    </div>
  </div>
</template>
