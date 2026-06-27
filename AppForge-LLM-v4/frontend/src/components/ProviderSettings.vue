<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  ApiError,
  deleteProvider,
  getActiveProvider,
  getProviders,
  saveProvider,
  setActiveProvider,
  testProvider,
} from '../api';
import type { ActiveSelection, ProviderStatus, TestResult } from '../types';

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

const draft = ref<Record<string, { apiKey: string; baseURL: string; defaultModel: string; useCustomModel: boolean }>>({});
const testResults = ref<Record<string, TestResult>>({});

const activeProviderId = ref<string>('');
const activeModel = ref<string>('');

const activeProvider = computed(() => providers.value.find((p) => p.id === activeProviderId.value) ?? null);
const activeModelOptions = computed(() => activeProvider.value?.models ?? []);

onMounted(async () => {
  await reload();
});

async function reload() {
  loading.value = true;
  loadError.value = '';
  try {
    const [providersPayload, activePayload] = await Promise.all([getProviders(), getActiveProvider()]);
    providers.value = providersPayload.providers;
    active.value = activePayload;
    activeProviderId.value = activePayload.provider ?? providers.value[0]?.id ?? '';
    activeModel.value = activePayload.model ?? '';
  } catch (error) {
    loadError.value = readableError(error, '프로바이더 목록을 불러오지 못했습니다.');
  } finally {
    loading.value = false;
  }
}

function ensureDraft(provider: ProviderStatus) {
  if (!draft.value[provider.id]) {
    draft.value[provider.id] = {
      apiKey: '',
      baseURL: provider.base_url ?? '',
      defaultModel: provider.default_model ?? '',
      useCustomModel: Boolean(provider.default_model) && !provider.models.some((m) => m.id === provider.default_model),
    };
  }
  return draft.value[provider.id];
}

function toggle(provider: ProviderStatus) {
  ensureDraft(provider);
  expandedId.value = expandedId.value === provider.id ? null : provider.id;
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

function modelSuggestions(provider: ProviderStatus) {
  return provider.models.map((m) => m.id);
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
  const model = d.defaultModel.trim() || provider.default_model || provider.models[0]?.id || '';
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

      <section v-if="!loading && !loadError" class="active-block">
        <h3>사용할 모델</h3>
        <p class="settings-hint">
          AppForge 파이프라인이 호출할 프로바이더와 모델입니다. <code>APPFORGE_DRIVER=llm-bridge</code> 로 실행할
          때 적용됩니다.
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
            <input
              v-model="activeModel"
              type="text"
              list="activeModelOptions"
              placeholder="예: gpt-4o-mini"
            />
            <datalist id="activeModelOptions">
              <option v-for="m in activeModelOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
            </datalist>
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

      <section v-if="!loading && !loadError" class="provider-list">
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
              <input
                v-model="ensureDraft(provider).defaultModel"
                :type="'text'"
                :list="`models-${provider.id}`"
                placeholder="모델 ID"
              />
              <datalist :id="`models-${provider.id}`">
                <option v-for="m in provider.models" :key="m.id" :value="m.id">{{ m.name }}</option>
              </datalist>
            </label>
            <div class="model-chips">
              <button
                v-for="id in modelSuggestions(provider)"
                :key="id"
                type="button"
                class="model-chip"
                @click="ensureDraft(provider).defaultModel = id"
              >
                {{ id }}
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
