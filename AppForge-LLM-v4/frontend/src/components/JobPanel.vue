<script setup lang="ts">
import { computed } from 'vue';
import type { JobPayload } from '../types';
import ErrorPanel from './ErrorPanel.vue';
import EventFeed from './EventFeed.vue';
import ProgressRing from './ProgressRing.vue';
import StageTimeline from './StageTimeline.vue';

const props = defineProps<{
  job: JobPayload | null;
}>();

const emit = defineEmits<{
  newRequest: [];
  toast: [message: string];
}>();

const activeStatuses = new Set(['queued', 'initializing', 'running', 'packaging']);

const isActive = computed(() => Boolean(props.job && activeStatuses.has(props.job.status)));

const statusTitle = computed(() => {
  const job = props.job;
  if (!job) return '앱 제작을 준비하고 있습니다.';
  if (job.status === 'completed') return '앱 제작이 완료되었습니다.';
  if (job.status === 'failed') return '확인이 필요한 오류가 발생했습니다.';
  if (job.status === 'packaging') return '다운로드 패키지를 준비하고 있습니다.';
  if (job.status === 'initializing' || job.status === 'queued') return '앱 제작을 준비하고 있습니다.';
  const activeStage = job.stages.find((stage) => stage.id === job.active_stage);
  return activeStage ? `${activeStage.name} 진행 중` : '앱을 만들고 있습니다.';
});

const completedStages = computed(() => props.job?.stages.filter((stage) => stage.status === 'completed').length || 0);
const totalStages = computed(() => props.job?.stages.length || 0);

const downloadUrl = computed(() => {
  const job = props.job;
  if (!job || job.status !== 'completed' || !job.download.available || !job.download.url) return '#';
  return job.download.url;
});

const downloadLabel = computed(() => {
  const job = props.job;
  if (!job) return '완료 후 ZIP 다운로드';
  if (job.status === 'failed') return '오류 해결 후 ZIP 다운로드';
  if (job.status !== 'completed' || !job.download.available) return '완료 후 ZIP 다운로드';
  const size = job.download.size_bytes ? ` · ${formatBytes(job.download.size_bytes)}` : '';
  return `소스 ZIP 다운로드${size}`;
});

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
        </div>
        <h2 id="jobStatusTitle">{{ statusTitle }}</h2>
        <p>{{ props.job.message || '상태를 확인하고 있습니다.' }}</p>
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
    </div>

    <div class="stage-heading">
      <h3>진행 단계</h3>
      <span v-if="props.job.updated_at" class="last-updated">업데이트 {{ formatDateTime(props.job.updated_at) }}</span>
    </div>

    <div class="job-grid">
      <StageTimeline :stages="props.job.stages" :active-stage="props.job.active_stage" />
      <EventFeed :events="props.job.events" />
    </div>

    <ErrorPanel :error="props.job.error" @copied="emit('toast', $event)" />

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
