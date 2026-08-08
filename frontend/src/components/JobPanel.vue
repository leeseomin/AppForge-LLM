<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import {
  approveJob,
  buildPreview,
  getSessionToken,
  retryJob,
  reviseJob,
} from '../api';
import { isActiveJobStatus } from '../jobStatus';
import type { JobPayload } from '../types';
import ArtifactBrowser from './ArtifactBrowser.vue';
import ErrorPanel from './ErrorPanel.vue';
import EventFeed from './EventFeed.vue';
import ProgressRing from './ProgressRing.vue';
import StageTimeline from './StageTimeline.vue';
import WorkspaceBrowser from './WorkspaceBrowser.vue';

const props = defineProps<{
  job: JobPayload | null;
}>();

const emit = defineEmits<{
  jobUpdated: [job: JobPayload];
  newRequest: [];
  toast: [message: string];
}>();

const buildingPreview = ref(false);
const previewError = ref('');
const localPreviewUrl = ref<string | null>(null);
const revisionText = ref('');
const revising = ref(false);
const approving = ref(false);
const retrying = ref(false);
const requestedArtifactName = ref('');

const isActive = computed(() => isActiveJobStatus(props.job?.status));
const isAwaitingApproval = computed(() => props.job?.status === 'awaiting_approval');

const statusTitle = computed(() => {
  const job = props.job;
  if (!job) return '앱 제작을 준비하고 있습니다.';
  if (job.status === 'completed') return '앱 제작이 완료되었습니다.';
  if (job.status === 'failed') return '확인이 필요한 오류가 발생했습니다.';
  if (job.status === 'awaiting_approval') return '승인이 필요한 체크포인트에서 대기 중입니다.';
  if (job.status === 'packaging') return '다운로드 패키지를 준비하고 있습니다.';
  if (job.status === 'queued') return job.queue_position ? `대기열 ${job.queue_position}번째입니다.` : '앱 제작을 준비하고 있습니다.';
  if (job.status === 'initializing') return '앱 제작을 준비하고 있습니다.';
  const activeStage = job.stages.find((stage) => stage.id === job.active_stage);
  return activeStage ? `${activeStage.name} 진행 중` : '앱을 만들고 있습니다.';
});

const completedStages = computed(() => props.job?.stages.filter((stage) => stage.status === 'completed').length || 0);
const totalStages = computed(() => props.job?.stages.length || 0);
const totalTokens = computed(() => props.job?.usage?.total_tokens || 0);
const estimatedCost = computed(() => props.job?.usage?.estimated_cost_usd);

const downloadUrl = computed(() => {
  const job = props.job;
  if (!job || job.status !== 'completed' || !job.download.available || !job.download.url) return '#';
  const url = new URL(job.download.url, window.location.origin);
  const token = getSessionToken();
  if (token) url.searchParams.set('token', token);
  return url.toString();
});

const downloadLabel = computed(() => {
  const job = props.job;
  if (!job) return '완료 후 ZIP 다운로드';
  if (job.status === 'failed') return '오류 해결 후 ZIP 다운로드';
  if (job.status !== 'completed' || !job.download.available) return '완료 후 ZIP 다운로드';
  const size = job.download.size_bytes ? ` · ${formatBytes(job.download.size_bytes)}` : '';
  return `소스 ZIP 다운로드${size}`;
});

const previewUrl = computed(() => localPreviewUrl.value || props.job?.preview?.url || null);
const canBuildPreview = computed(() => Boolean(props.job && !isActive.value && props.job.project_path));
const canInspectWorkspace = computed(() => Boolean(props.job?.project_path));
const canRevise = computed(() => Boolean(props.job && !isActive.value && props.job.project_path && props.job.id !== 'request-error'));

watch(
  () => props.job?.id,
  () => {
    localPreviewUrl.value = null;
    previewError.value = '';
    revisionText.value = '';
    requestedArtifactName.value = '';
  },
);

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return '';
  if (value < 1024) return `${value.toLocaleString('ko-KR')} B`;
  const units = ['KB', 'MB', 'GB'];
  let amount = value / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
}

function formatDateTime(value: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

function formatTokens(value?: number) {
  return Number.isFinite(value) ? Number(value).toLocaleString('ko-KR') : '-';
}

function formatCost(value?: number) {
  return Number.isFinite(value) ? `$${Number(value).toFixed(6)}` : '가격 정보 없음';
}

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

async function onBuildPreview() {
  if (!props.job || buildingPreview.value) return;
  buildingPreview.value = true;
  previewError.value = '';
  try {
    const preview = await buildPreview(props.job.id);
    localPreviewUrl.value = preview.url;
    emit('toast', '정적 프리뷰를 준비했습니다.');
  } catch (error) {
    previewError.value = readableError(error, '프리뷰를 만들지 못했습니다.');
    emit('toast', previewError.value);
  } finally {
    buildingPreview.value = false;
  }
}

function onOpenArtifact(name: string) {
  requestedArtifactName.value = name;
}

async function onApprove() {
  if (!props.job || approving.value) return;
  approving.value = true;
  try {
    const updated = await approveJob(props.job.id);
    emit('jobUpdated', updated);
    emit('toast', '승인을 기록하고 파이프라인을 계속합니다.');
  } catch (error) {
    emit('toast', readableError(error, '승인을 처리하지 못했습니다.'));
  } finally {
    approving.value = false;
  }
}

async function onRetry(stage: string | null = null) {
  if (!props.job || retrying.value) return;
  retrying.value = true;
  try {
    const updated = await retryJob(props.job.id, stage || props.job.active_stage || undefined);
    emit('jobUpdated', updated);
    emit('toast', '재시도 작업을 대기열에 등록했습니다.');
  } catch (error) {
    emit('toast', readableError(error, '재시도를 시작하지 못했습니다.'));
  } finally {
    retrying.value = false;
  }
}

async function onSubmitRevision() {
  if (!props.job || revising.value) return;
  const requestText = revisionText.value.trim();
  if (!requestText) {
    emit('toast', '수정 요청 내용을 입력해 주세요.');
    return;
  }
  revising.value = true;
  try {
    const created = await reviseJob(props.job.id, requestText);
    revisionText.value = '';
    emit('jobUpdated', created);
    emit('toast', '수정 작업을 대기열에 등록했습니다.');
  } catch (error) {
    emit('toast', readableError(error, '수정 작업을 만들지 못했습니다.'));
  } finally {
    revising.value = false;
  }
}

function onDownloadClick(event: MouseEvent) {
  if (downloadUrl.value === '#') {
    event.preventDefault();
  }
}
</script>

<template>
  <section v-if="props.job" class="job-panel" aria-labelledby="jobStatusTitle">
    <div class="status-header">
      <div class="status-copy">
        <div class="status-line">
          <span
            class="status-pill"
            :class="{
              'is-completed': props.job.status === 'completed',
              'is-failed': props.job.status === 'failed',
            }"
          >
            {{ props.job.status_label || props.job.status }}
          </span>
          <span v-if="props.job.pipeline" class="pipeline-label">자동 파이프라인 · {{ props.job.pipeline }}</span>
          <span v-if="props.job.mode" class="pipeline-label">{{ props.job.mode === 'checkpoint' ? '체크포인트' : '자율' }} 모드</span>
          <span v-if="props.job.queue_position" class="pipeline-label">대기열 #{{ props.job.queue_position }}</span>
          <span v-if="props.job.revision_index" class="pipeline-label">수정 #{{ props.job.revision_index }}</span>
          <span v-if="props.job.llm?.provider || props.job.llm?.model" class="pipeline-label">
            {{ [props.job.llm?.provider, props.job.llm?.model].filter(Boolean).join(' / ') }}
          </span>
        </div>
        <h2 id="jobStatusTitle">{{ statusTitle }}</h2>
        <p>{{ props.job.message || '상태를 확인하고 있습니다.' }}</p>
        <p v-if="props.job.routing?.rationale" class="routing-rationale">
          라우팅: {{ props.job.routing.rationale }}
        </p>
      </div>
      <ProgressRing :value="props.job.progress" />
    </div>

    <div
      class="progress-track"
      role="progressbar"
      aria-label="전체 진행률"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="props.job.progress"
    >
      <div class="progress-bar" :style="{ width: `${props.job.progress}%` }"></div>
    </div>

    <div class="job-metrics" aria-label="작업 요약">
      <div>
        <span>완료 단계</span>
        <strong>{{ completedStages }} / {{ totalStages }}</strong>
      </div>
      <div>
        <span>작업 ID</span>
        <strong>{{ props.job.id.slice(0, 8) }}</strong>
      </div>
      <div>
        <span>최근 업데이트</span>
        <strong>{{ formatDateTime(props.job.updated_at) || '-' }}</strong>
      </div>
      <div>
        <span>사용 토큰</span>
        <strong>{{ formatTokens(totalTokens) }}</strong>
      </div>
      <div>
        <span>추정 비용 (USD)</span>
        <strong>{{ formatCost(estimatedCost) }}</strong>
      </div>
    </div>

    <details v-if="totalTokens" class="usage-breakdown">
      <summary>토큰 사용량 상세</summary>
      <dl>
        <div><dt>입력</dt><dd>{{ formatTokens(props.job.usage?.input_tokens) }}</dd></div>
        <div><dt>출력</dt><dd>{{ formatTokens(props.job.usage?.output_tokens) }}</dd></div>
        <div><dt>캐시 읽기</dt><dd>{{ formatTokens(props.job.usage?.cache_read_input_tokens) }}</dd></div>
        <div><dt>캐시 쓰기</dt><dd>{{ formatTokens(props.job.usage?.cache_write_input_tokens) }}</dd></div>
        <div><dt>추론</dt><dd>{{ formatTokens(props.job.usage?.reasoning_tokens) }}</dd></div>
      </dl>
      <p>비용은 models.dev 카탈로그 단가가 제공된 모델에 한해 추정됩니다.</p>
    </details>

    <div v-if="isAwaitingApproval" class="approval-panel">
      <div>
        <h3>중간 산출물 승인 필요</h3>
        <p>아래 아티팩트와 코드를 확인한 뒤 계속 진행할 수 있습니다. 수정이 필요하면 수정 요청을 등록하세요.</p>
      </div>
      <button class="primary-button" type="button" :disabled="approving" @click="onApprove">
        {{ approving ? '승인 중…' : '검토 완료 · 계속 진행' }}
      </button>
    </div>

    <div class="stage-heading">
      <h3>진행 단계</h3>
      <span v-if="props.job.updated_at" class="last-updated">업데이트 {{ formatDateTime(props.job.updated_at) }}</span>
    </div>

    <div class="job-grid">
      <StageTimeline
        :stages="props.job.stages"
        :active-stage="props.job.active_stage"
        @open-artifact="onOpenArtifact"
      />
      <EventFeed :events="props.job.events" />
    </div>

    <ErrorPanel
      :error="props.job.error"
      :can-retry="Boolean(props.job.error?.stage && !isActive)"
      @copied="emit('toast', $event)"
      @retry="onRetry"
    />

    <div v-if="canInspectWorkspace" class="inspector-grid">
      <WorkspaceBrowser :job-id="props.job.id" @toast="emit('toast', $event)" />
      <ArtifactBrowser
        :job-id="props.job.id"
        :initial-artifact="requestedArtifactName"
        @toast="emit('toast', $event)"
      />
    </div>

    <div class="preview-panel" v-if="canBuildPreview || previewUrl">
      <div class="preview-header">
        <div>
          <h3>정적 프리뷰</h3>
          <p>빌드 산출물이 있으면 같은 화면에서 생성 앱을 확인합니다.</p>
        </div>
        <button class="secondary-button" type="button" :disabled="buildingPreview" @click="onBuildPreview">
          {{ buildingPreview ? '빌드 중' : previewUrl ? '프리뷰 다시 빌드' : '프리뷰 빌드' }}
        </button>
      </div>
      <p v-if="previewError" class="preview-error">{{ previewError }}</p>
      <iframe
        v-if="previewUrl"
        class="preview-frame"
        title="생성 앱 프리뷰"
        :src="previewUrl"
        sandbox="allow-scripts"
      ></iframe>
    </div>

    <div v-if="canRevise" class="revision-panel">
      <div>
        <h3>대화형 수정 요청</h3>
        <p>프리뷰나 코드를 확인한 뒤 바꾸고 싶은 점을 적으면 기존 작업공간을 기준으로 수정 작업을 만듭니다.</p>
      </div>
      <textarea
        v-model="revisionText"
        rows="4"
        placeholder="예: 로그인 버튼을 더 눈에 띄게 만들고, 모바일에서 카드 간격을 줄여 주세요."
        :disabled="revising"
      ></textarea>
      <button class="primary-button" type="button" :disabled="revising" @click="onSubmitRevision">
        {{ revising ? '등록 중…' : '수정 작업 생성' }}
      </button>
    </div>

    <div class="result-actions">
      <a
        class="download-button"
        :class="{ 'is-disabled': downloadUrl === '#' }"
        :href="downloadUrl"
        role="button"
        :aria-disabled="downloadUrl === '#' ? 'true' : 'false'"
        :tabindex="downloadUrl === '#' ? -1 : undefined"
        @click="onDownloadClick"
      >
        <span class="download-icon" aria-hidden="true">↓</span>
        <span>{{ downloadLabel }}</span>
      </a>
      <button v-if="!isActive" class="secondary-button" type="button" @click="emit('newRequest')">
        새 요청 시작
      </button>
    </div>
  </section>
</template>
