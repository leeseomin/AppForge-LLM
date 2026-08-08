<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import {
  ApiError,
  cancelJob,
  createJob,
  endSession,
  getHealth,
  getJob,
} from './api';
import ComposerCard from './components/ComposerCard.vue';
import HealthBanner from './components/HealthBanner.vue';
import JobHistory from './components/JobHistory.vue';
import JobPanel from './components/JobPanel.vue';
import ProviderSettings from './components/ProviderSettings.vue';
import Toast from './components/Toast.vue';
import TopBar from './components/TopBar.vue';
import { useI18n } from './i18n';
import { isActiveJobStatus } from './jobStatus';
import type { ApiErrorPayload, HealthPayload, JobPayload, JobRunSettings } from './types';

const { locale, setLocale, t } = useI18n();

const STORAGE_KEY = 'appforge-v7-current-job';
const LEGACY_STORAGE_KEY = 'appforge-v6-current-job';
const storedJobId = window.localStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem(LEGACY_STORAGE_KEY);
if (storedJobId && !window.localStorage.getItem(STORAGE_KEY)) {
  window.localStorage.setItem(STORAGE_KEY, storedJobId);
  window.localStorage.removeItem(LEGACY_STORAGE_KEY);
}

const health = ref<HealthPayload | null>(null);
const serverError = ref('');
const currentJobId = ref<string | null>(storedJobId);
const job = ref<JobPayload | null>(null);
const prompt = ref('');
const runMode = ref('autonomous');
const submitting = ref(false);
const cancelling = ref(false);
const endingSession = ref(false);
const toastMessage = ref('');
const settingsOpen = ref(false);
const historyOpen = ref(false);
const jobSettings = ref<JobRunSettings>({
  provider: null,
  model: null,
  generation: {},
  pricing: {},
});

let pollTimer: number | undefined;
let toastTimer: number | undefined;
let eventSource: EventSource | undefined;
let eventJobId: string | null = null;
const lastEventIds = new Map<string, string>();

function normalizeJobSettings(settings?: JobPayload['llm']): JobRunSettings {
  return {
    provider: settings?.provider || null,
    model: settings?.model || null,
    generation: settings?.generation || {},
    pricing: settings?.pricing || {},
  };
}

type LoadCurrentJobOptions = {
  immediate?: boolean;
  restoreTerminal?: boolean;
};

const promptMaxChars = computed(() => {
  const configured = Number(health.value?.prompt_max_chars);
  return Number.isFinite(configured) && configured > 0 ? configured : 20_000;
});

const isActiveJob = computed(() => isActiveJobStatus(job.value?.status));
const canStart = computed(() => Boolean(health.value?.ready));

function setCurrentJob(jobId: string | null) {
  currentJobId.value = jobId;
  if (jobId) {
    window.localStorage.setItem(STORAGE_KEY, jobId);
    openEventStream(jobId);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    closeEventStream();
  }
}

async function refreshHealth(options: { restoreBusyJob?: boolean } = {}) {
  const restoreBusyJob = options.restoreBusyJob ?? true;
  if (endingSession.value) return;
  try {
    const payload = await getHealth();
    if (endingSession.value) return;
    health.value = payload;
    serverError.value = '';
    if (
      restoreBusyJob &&
      payload.active_job_id &&
      (!currentJobId.value || currentJobId.value !== payload.active_job_id)
    ) {
      setCurrentJob(payload.active_job_id);
      await loadCurrentJob({ immediate: true });
    }
  } catch (error) {
    if (endingSession.value) {
      serverError.value = t('topbar.sessionEnded');
      return;
    }
    health.value = null;
    serverError.value = readableError(error, t('app.serverConnectError'));
  }
}

async function submitJob() {
  if (submitting.value || isActiveJob.value) return;
  const normalized = prompt.value.trim();
  if (!normalized) {
    showToast(t('composer.required'));
    return;
  }
  if (!health.value?.ready) {
    showToast(t('health.notReady'));
    return;
  }

  submitting.value = true;
  try {
    const created = await createJob(normalized, {
      mode: runMode.value,
      provider: jobSettings.value.provider,
      model: jobSettings.value.model,
      generation: jobSettings.value.generation,
      pricing: jobSettings.value.pricing,
    });
    job.value = created;
    jobSettings.value = normalizeJobSettings(created.llm);
    setCurrentJob(created.id);
    schedulePoll(350);
    await nextTick();
    document.querySelector('#jobStatusTitle')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    if (error instanceof ApiError) {
      const runningJobId = error.payload.context?.current_job_id;
      if (typeof runningJobId === 'string' && runningJobId) {
        setCurrentJob(runningJobId);
        await loadCurrentJob({ immediate: true });
        showToast(t('app.existingJob'));
      } else {
        job.value = makeRequestErrorJob(error.payload, normalized);
        showToast(error.payload.title || t('app.requestStartError'));
      }
    } else {
      job.value = makeRequestErrorJob(
        {
          code: 'REQUEST_FAILED',
          title: t('app.requestStartTitle'),
          message: readableError(error, t('app.requestProcessError')),
          action: t('app.requestAction'),
          technical: {},
        },
        normalized,
      );
    }
  } finally {
    submitting.value = false;
  }
}

function schedulePoll(delay = 1000) {
  window.clearTimeout(pollTimer);
  if (!currentJobId.value) return;
  pollTimer = window.setTimeout(() => loadCurrentJob({ immediate: false }), delay);
}


function closeEventStream() {
  if (eventSource) {
    eventSource.close();
    eventSource = undefined;
  }
  eventJobId = null;
}

function openEventStream(jobId: string) {
  if (typeof EventSource === 'undefined') return;
  if (eventSource && eventJobId === jobId) return;
  closeEventStream();
  eventJobId = jobId;
  const url = new URL(`/api/jobs/${encodeURIComponent(jobId)}/events`, window.location.origin);
  const lastEventId = lastEventIds.get(jobId);
  if (lastEventId) url.searchParams.set('lastEventId', lastEventId);
  eventSource = new EventSource(url.toString());

  const refreshFromEvent = (event: MessageEvent) => {
    try {
      if (event.lastEventId) {
        lastEventIds.set(jobId, event.lastEventId);
      }
      const payload = JSON.parse(event.data || '{}');
      if (payload.event === 'event_gap') {
        loadCurrentJob({ immediate: true });
        return;
      }
      if (payload.job) {
        job.value = payload.job;
        if (!prompt.value && payload.job.prompt) {
          prompt.value = payload.job.prompt;
        }
        if (payload.job.terminal) {
          window.clearTimeout(pollTimer);
          setCurrentJob(null);
          refreshHealth({ restoreBusyJob: false });
        }
        return;
      }
      if (payload.event === 'job_completed' || payload.event === 'job_failed' || payload.event === 'job_cancelled') {
        loadCurrentJob({ immediate: true });
        closeEventStream();
        return;
      }
      if (currentJobId.value === jobId) {
        loadCurrentJob({ immediate: true });
      }
    } catch {
      if (currentJobId.value === jobId) {
        loadCurrentJob({ immediate: true });
      }
    }
  };

  [
    'snapshot',
    'job_started',
    'stage_started',
    'stage_completed',
    'stage_retrying',
    'loop_guard_triggered',
    'stage_awaiting_approval',
    'job_awaiting_approval',
    'job_approved',
    'job_dequeued',
    'revision_queued',
    'stage_retry_requested',
    'stage_failed',
    'event_gap',
    'job_completed',
    'job_failed',
    'job_cancelled',
    'preview_built',
    'llm_text',
    'tool_call',
    'bridge_recovering',
  ].forEach((name) => eventSource?.addEventListener(name, refreshFromEvent as EventListener));

  eventSource.onerror = () => {
    closeEventStream();
    if (currentJobId.value === jobId && isActiveJob.value) {
      schedulePoll(1500);
    }
  };
}

async function loadCurrentJob({ immediate = false, restoreTerminal = true }: LoadCurrentJobOptions = {}) {
  if (endingSession.value) return;
  if (!currentJobId.value) return;
  try {
    const payload = await getJob(currentJobId.value);
    if (endingSession.value) return;
    if (!restoreTerminal && payload.terminal) {
      setCurrentJob(null);
      job.value = null;
      return;
    }
    job.value = payload;
    jobSettings.value = normalizeJobSettings(payload.llm);
    if (!prompt.value && payload.prompt) {
      prompt.value = payload.prompt;
    }
    if (isActiveJobStatus(payload.status)) {
      schedulePoll(immediate ? 700 : 1100);
    } else {
      window.clearTimeout(pollTimer);
      setCurrentJob(null);
      await refreshHealth({ restoreBusyJob: false });
    }
  } catch (error) {
    if (endingSession.value) {
      serverError.value = t('topbar.sessionEnded');
      return;
    }
    if (error instanceof ApiError && error.status === 404) {
      setCurrentJob(null);
      job.value = null;
      showToast(t('app.previousJobMissing'));
      return;
    }
    serverError.value = readableError(error, t('app.jobLoadError'));
    schedulePoll(3000);
  }
}

async function cancelActiveJob() {
  if (!currentJobId.value || !isActiveJob.value || cancelling.value) return;
  cancelling.value = true;
  try {
    const payload = await cancelJob(currentJobId.value);
    job.value = payload;
    window.clearTimeout(pollTimer);
    setCurrentJob(null);
    await refreshHealth({ restoreBusyJob: false });
    showToast(t('app.cancelled'));
  } catch (error) {
    showToast(readableError(error, t('app.cancelError')));
    schedulePoll(1200);
  } finally {
    cancelling.value = false;
  }
}

function handleJobUpdated(updated: JobPayload) {
  job.value = updated;
  jobSettings.value = normalizeJobSettings(updated.llm);
  if (!updated.terminal && updated.id !== 'request-error') {
    setCurrentJob(updated.id);
    schedulePoll(350);
  }
}

async function showHistoricalJob(jobId: string) {
  if (isActiveJob.value && currentJobId.value !== jobId) {
    showToast(t('app.historyBusy'));
    return;
  }
  try {
    const selected = await getJob(jobId);
    job.value = selected;
    prompt.value = selected.prompt || '';
    jobSettings.value = normalizeJobSettings(selected.llm);
    if (isActiveJobStatus(selected.status)) {
      setCurrentJob(selected.id);
      schedulePoll(350);
    } else {
      window.clearTimeout(pollTimer);
      setCurrentJob(null);
    }
    historyOpen.value = false;
    await nextTick();
    document.querySelector('#jobStatusTitle')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    showToast(readableError(error, t('app.historyOpenError')));
  }
}

function handleHistoryRerun(created: JobPayload) {
  historyOpen.value = false;
  prompt.value = created.prompt || '';
  handleJobUpdated(created);
}

async function endCurrentSession() {
  if (endingSession.value) return;
  endingSession.value = true;
  window.clearTimeout(pollTimer);
  setCurrentJob(null);
  job.value = null;
  prompt.value = '';
  health.value = null;
  serverError.value = t('topbar.sessionEnded');
  try {
    const payload = await endSession();
    showToast(payload.message || t('app.sessionEnding'));
    serverError.value = t('topbar.sessionEnded');
    health.value = null;
  } catch (error) {
    endingSession.value = false;
    showToast(readableError(error, t('app.sessionEndError')));
    schedulePoll(1200);
  }
}

function startNewRequest() {
  window.clearTimeout(pollTimer);
  job.value = null;
  prompt.value = '';
  setCurrentJob(null);
  nextTick(() => {
    document.querySelector<HTMLTextAreaElement>('#promptInput')?.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}

function openProviderSettings() {
  settingsOpen.value = true;
}

function openHistory() {
  historyOpen.value = true;
}

function closeProviderSettings() {
  settingsOpen.value = false;
}

async function handleProviderSettingsChanged() {
  await refreshHealth({ restoreBusyJob: false });
}

function readableError(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.payload.message || error.payload.title || fallback;
  }
  if (error instanceof Error) {
    return error.message || fallback;
  }
  return fallback;
}

function makeRequestErrorJob(error: ApiErrorPayload, requestPrompt: string): JobPayload {
  const now = new Date().toISOString();
  return {
    id: 'request-error',
    version: '2.0',
    prompt: requestPrompt,
    status: 'failed',
    status_label: t('app.requestErrorLabel'),
    message: error.message || t('app.requestProcessError'),
    created_at: now,
    updated_at: now,
    started_at: null,
    completed_at: now,
    pipeline: null,
    pipeline_description: null,
    driver: null,
    project_name: null,
    project_path: null,
    active_stage: null,
    stages: [],
    events: [],
    error,
    download: {
      available: false,
      url: null,
      filename: null,
      size_bytes: null,
    },
    progress: 0,
    terminal: true,
  };
}

function showToast(message: string) {
  window.clearTimeout(toastTimer);
  toastMessage.value = message;
  toastTimer = window.setTimeout(() => {
    toastMessage.value = '';
  }, 3000);
}

function handleVisibilityChange() {
  if (!document.hidden && isActiveJob.value) {
    loadCurrentJob({ immediate: true });
  }
}

onMounted(async () => {
  document.addEventListener('visibilitychange', handleVisibilityChange);
  await refreshHealth();
  if (currentJobId.value && !job.value) {
    await loadCurrentJob({ immediate: true, restoreTerminal: false });
  }
});

onBeforeUnmount(() => {
  window.clearTimeout(pollTimer);
  window.clearTimeout(toastTimer);
  closeEventStream();
  document.removeEventListener('visibilitychange', handleVisibilityChange);
});
</script>

<template>
  <div class="background-grid" aria-hidden="true"></div>
  <div class="app-shell">
    <aside class="sidebar">
      <TopBar
        :health="health"
        :server-error="serverError"
        :can-cancel="isActiveJob"
        :cancelling="cancelling"
        :ending-session="endingSession"
        @refresh="refreshHealth"
        @open-history="openHistory"
        @open-settings="openProviderSettings"
        @cancel="cancelActiveJob"
        @end-session="endCurrentSession"
      />
    </aside>

    <main class="content">
      <section class="hero" aria-labelledby="heroTitle">
        <div>
          <p class="eyebrow">{{ t('hero.eyebrow') }}</p>
          <h1 id="heroTitle">{{ t('hero.title') }}</h1>
          <p class="hero-copy">{{ t('hero.description') }}</p>
        </div>
        <div class="hero-aside">
          <div class="language-switch" role="group" :aria-label="t('language.label')">
            <button
              type="button"
              :class="{ selected: locale === 'ko' }"
              :aria-pressed="locale === 'ko'"
              @click="setLocale('ko')"
            >
              {{ t('language.korean') }}
            </button>
            <button
              type="button"
              :class="{ selected: locale === 'en' }"
              :aria-pressed="locale === 'en'"
              @click="setLocale('en')"
            >
              {{ t('language.english') }}
            </button>
          </div>
          <div class="hero-flow" :aria-label="t('hero.workflowLabel')">
            <span><b>01</b>{{ t('hero.plan') }}</span>
            <span><b>02</b>{{ t('hero.build') }}</span>
            <span><b>03</b>{{ t('hero.verify') }}</span>
          </div>
        </div>
      </section>

      <HealthBanner id="readinessNotice" :health="health" :server-error="serverError" />

      <div class="workspace-layout">
        <ComposerCard
          v-model="prompt"
          :prompt-max-chars="promptMaxChars"
          :ready="canStart"
          :busy="isActiveJob"
          :submitting="submitting"
          :mode="runMode"
          :settings="jobSettings"
          @update:mode="runMode = $event"
          @update:settings="jobSettings = $event"
          @submit="submitJob"
        />
        <JobPanel :job="job" @job-updated="handleJobUpdated" @new-request="startNewRequest" @toast="showToast" />
      </div>

      <footer class="footer-note">
        <span aria-hidden="true">●</span>
        <p>{{ t('app.footer') }}</p>
      </footer>
    </main>
  </div>
  <ProviderSettings
    v-if="settingsOpen"
    @close="closeProviderSettings"
    @toast="showToast"
    @changed="handleProviderSettingsChanged"
  />
  <JobHistory
    v-if="historyOpen"
    :current-job-id="currentJobId"
    :busy="isActiveJob"
    @close="historyOpen = false"
    @select="showHistoricalJob"
    @rerun="handleHistoryRerun"
    @toast="showToast"
  />
  <Toast :message="toastMessage" />
</template>
