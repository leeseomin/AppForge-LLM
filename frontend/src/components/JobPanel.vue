<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import {
  approveJob,
  buildPreview,
  getSessionToken,
  retryJob,
  reviseJob,
} from '../api';
import { useI18n } from '../i18n';
import { isActiveJobStatus } from '../jobStatus';
import type { JobPayload } from '../types';
import ArtifactBrowser from './ArtifactBrowser.vue';
import ErrorPanel from './ErrorPanel.vue';
import EventFeed from './EventFeed.vue';
import ProgressRing from './ProgressRing.vue';
import StageTimeline from './StageTimeline.vue';
import WorkspaceBrowser from './WorkspaceBrowser.vue';

const { locale, t } = useI18n();

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
  if (!job) return t('job.preparing');
  if (job.status === 'completed') return t('job.completed');
  if (job.status === 'failed') return t('job.failed');
  if (job.status === 'awaiting_approval') return t('job.awaitingApproval');
  if (job.status === 'packaging') return t('job.packaging');
  if (job.status === 'queued') return job.queue_position ? t('job.queued', { position: job.queue_position }) : t('job.preparing');
  if (job.status === 'initializing') return t('job.preparing');
  const activeStage = job.stages.find((stage) => stage.id === job.active_stage);
  return activeStage ? t('job.stageRunning', { name: activeStage.name }) : t('job.building');
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
  if (!job) return t('job.downloadPending');
  if (job.status === 'failed') return t('job.downloadAfterFix');
  if (job.status !== 'completed' || !job.download.available) return t('job.downloadPending');
  const size = job.download.size_bytes ? ` · ${formatBytes(job.download.size_bytes)}` : '';
  return t('job.downloadReady', { size });
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
  const numberLocale = locale.value === 'ko' ? 'ko-KR' : 'en-US';
  if (value < 1024) return `${value.toLocaleString(numberLocale)} B`;
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
  return new Intl.DateTimeFormat(locale.value === 'ko' ? 'ko-KR' : 'en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

function formatTokens(value?: number) {
  return Number.isFinite(value) ? Number(value).toLocaleString(locale.value === 'ko' ? 'ko-KR' : 'en-US') : '-';
}

function formatCost(value?: number) {
  return Number.isFinite(value) ? `$${Number(value).toFixed(6)}` : t('job.noPricing');
}

function statusLabel(status: string, fallback?: string) {
  if (status === 'pending') return t('stage.pending');
  if (status === 'running') return t('stage.running');
  if (status === 'validating') return t('stage.validating');
  if (status === 'retrying') return t('stage.retrying');
  if (status === 'awaiting_approval') return t('stage.awaitingApproval');
  if (status === 'completed') return t('stage.completed');
  if (status === 'failed') return t('stage.failed');
  if (status === 'queued') return t('stage.queued');
  if (status === 'initializing') return t('stage.initializing');
  if (status === 'packaging') return t('stage.packaging');
  if (status === 'cancelled') return t('stage.cancelled');
  return fallback || status;
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
    emit('toast', t('job.previewReady'));
  } catch (error) {
    previewError.value = readableError(error, t('job.previewError'));
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
    emit('toast', t('job.approvalRecorded'));
  } catch (error) {
    emit('toast', readableError(error, t('job.approvalError')));
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
    emit('toast', t('job.retryQueued'));
  } catch (error) {
    emit('toast', readableError(error, t('job.retryError')));
  } finally {
    retrying.value = false;
  }
}

async function onSubmitRevision() {
  if (!props.job || revising.value) return;
  const requestText = revisionText.value.trim();
  if (!requestText) {
    emit('toast', t('job.revisionRequired'));
    return;
  }
  revising.value = true;
  try {
    const created = await reviseJob(props.job.id, requestText);
    revisionText.value = '';
    emit('jobUpdated', created);
    emit('toast', t('job.revisionQueued'));
  } catch (error) {
    emit('toast', readableError(error, t('job.revisionError')));
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
            {{ statusLabel(props.job.status, props.job.status_label) }}
          </span>
          <span v-if="props.job.pipeline" class="pipeline-label">{{ t('job.pipeline', { name: props.job.pipeline }) }}</span>
          <span v-if="props.job.mode" class="pipeline-label">{{ props.job.mode === 'checkpoint' ? t('job.modeCheckpoint') : t('job.modeAutonomous') }}</span>
          <span v-if="props.job.queue_position" class="pipeline-label">{{ t('job.queue', { position: props.job.queue_position }) }}</span>
          <span v-if="props.job.revision_index" class="pipeline-label">{{ t('job.revision', { index: props.job.revision_index }) }}</span>
          <span v-if="props.job.llm?.provider || props.job.llm?.model" class="pipeline-label">
            {{ [props.job.llm?.provider, props.job.llm?.model].filter(Boolean).join(' / ') }}
          </span>
        </div>
        <h2 id="jobStatusTitle">{{ statusTitle }}</h2>
        <p>{{ props.job.message || t('job.statusFallback') }}</p>
        <p v-if="props.job.routing?.rationale" class="routing-rationale">
          {{ t('job.routing', { rationale: props.job.routing.rationale }) }}
        </p>
      </div>
      <ProgressRing :value="props.job.progress" />
    </div>

    <div
      class="progress-track"
      role="progressbar"
      :aria-label="t('job.progressLabel')"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="props.job.progress"
    >
      <div class="progress-bar" :style="{ width: `${props.job.progress}%` }"></div>
    </div>

    <div class="job-metrics" :aria-label="t('job.summaryLabel')">
      <div>
        <span>{{ t('job.completedStages') }}</span>
        <strong>{{ completedStages }} / {{ totalStages }}</strong>
      </div>
      <div>
        <span>{{ t('job.id') }}</span>
        <strong>{{ props.job.id.slice(0, 8) }}</strong>
      </div>
      <div>
        <span>{{ t('job.updatedAt') }}</span>
        <strong>{{ formatDateTime(props.job.updated_at) || '-' }}</strong>
      </div>
      <div>
        <span>{{ t('job.tokens') }}</span>
        <strong>{{ formatTokens(totalTokens) }}</strong>
      </div>
      <div>
        <span>{{ t('job.cost') }}</span>
        <strong>{{ formatCost(estimatedCost) }}</strong>
      </div>
    </div>

    <details v-if="totalTokens" class="usage-breakdown">
      <summary>{{ t('job.usageDetails') }}</summary>
      <dl>
        <div><dt>{{ t('job.inputTokens') }}</dt><dd>{{ formatTokens(props.job.usage?.input_tokens) }}</dd></div>
        <div><dt>{{ t('job.outputTokens') }}</dt><dd>{{ formatTokens(props.job.usage?.output_tokens) }}</dd></div>
        <div><dt>{{ t('job.cacheRead') }}</dt><dd>{{ formatTokens(props.job.usage?.cache_read_input_tokens) }}</dd></div>
        <div><dt>{{ t('job.cacheWrite') }}</dt><dd>{{ formatTokens(props.job.usage?.cache_write_input_tokens) }}</dd></div>
        <div><dt>{{ t('job.reasoning') }}</dt><dd>{{ formatTokens(props.job.usage?.reasoning_tokens) }}</dd></div>
      </dl>
      <p>{{ t('job.pricingNote') }}</p>
    </details>

    <div v-if="isAwaitingApproval" class="approval-panel">
      <div>
        <h3>{{ t('job.approvalTitle') }}</h3>
        <p>{{ t('job.approvalHelp') }}</p>
      </div>
      <button class="primary-button" type="button" :disabled="approving" @click="onApprove">
        {{ approving ? t('job.approving') : t('job.approve') }}
      </button>
    </div>

    <div class="stage-heading">
      <h3>{{ t('job.stages') }}</h3>
      <span v-if="props.job.updated_at" class="last-updated">{{ t('job.updated', { time: formatDateTime(props.job.updated_at) }) }}</span>
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
          <h3>{{ t('job.previewTitle') }}</h3>
          <p>{{ t('job.previewHelp') }}</p>
        </div>
        <button class="secondary-button" type="button" :disabled="buildingPreview" @click="onBuildPreview">
          {{ buildingPreview ? t('job.previewBuilding') : previewUrl ? t('job.previewRebuild') : t('job.previewBuild') }}
        </button>
      </div>
      <p v-if="previewError" class="preview-error">{{ previewError }}</p>
      <iframe
        v-if="previewUrl"
        class="preview-frame"
        :title="t('job.previewFrameTitle')"
        :src="previewUrl"
        sandbox="allow-scripts"
      ></iframe>
    </div>

    <div v-if="canRevise" class="revision-panel">
      <div>
        <h3>{{ t('job.reviseTitle') }}</h3>
        <p>{{ t('job.reviseHelp') }}</p>
      </div>
      <textarea
        v-model="revisionText"
        rows="4"
        :placeholder="t('job.revisePlaceholder')"
        :disabled="revising"
      ></textarea>
      <button class="primary-button" type="button" :disabled="revising" @click="onSubmitRevision">
        {{ revising ? t('job.revising') : t('job.createRevision') }}
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
        {{ t('job.newRequest') }}
      </button>
    </div>
  </section>

  <section v-else class="job-panel empty-job-panel" aria-labelledby="emptyJobTitle">
    <div class="empty-job-copy">
      <p class="section-kicker">{{ t('job.emptyKicker') }}</p>
      <h2 id="emptyJobTitle">{{ t('job.emptyTitle') }}</h2>
      <p>{{ t('job.emptyHelp') }}</p>
    </div>
    <ol class="empty-pipeline" :aria-label="t('job.emptyFlowLabel')">
      <li>
        <span>01</span>
        <div><strong>{{ t('job.emptyPlan') }}</strong><small>{{ t('job.emptyPlanHelp') }}</small></div>
      </li>
      <li>
        <span>02</span>
        <div><strong>{{ t('job.emptyBuild') }}</strong><small>{{ t('job.emptyBuildHelp') }}</small></div>
      </li>
      <li>
        <span>03</span>
        <div><strong>{{ t('job.emptyPackage') }}</strong><small>{{ t('job.emptyPackageHelp') }}</small></div>
      </li>
    </ol>
    <div class="empty-job-note">
      <span aria-hidden="true">✓</span>
      <p>{{ t('job.emptyNote') }}</p>
    </div>
  </section>
</template>
