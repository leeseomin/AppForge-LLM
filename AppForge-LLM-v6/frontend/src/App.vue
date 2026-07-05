<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError, cancelJob, createJob, endSession, getHealth, getJob } from './api';
import ComposerCard from './components/ComposerCard.vue';
import HealthBanner from './components/HealthBanner.vue';
import JobPanel from './components/JobPanel.vue';
import ProviderSettings from './components/ProviderSettings.vue';
import Toast from './components/Toast.vue';
import TopBar from './components/TopBar.vue';
import { isActiveJobStatus } from './jobStatus';
import type { ApiErrorPayload, HealthPayload, JobPayload } from './types';

const STORAGE_KEY = 'appforge-v6-current-job';

const health = ref<HealthPayload | null>(null);
const serverError = ref('');
const currentJobId = ref<string | null>(window.localStorage.getItem(STORAGE_KEY));
const job = ref<JobPayload | null>(null);
const prompt = ref('');
const runMode = ref('autonomous');
const submitting = ref(false);
const cancelling = ref(false);
const endingSession = ref(false);
const toastMessage = ref('');
const settingsOpen = ref(false);

let pollTimer: number | undefined;
let toastTimer: number | undefined;
let eventSource: EventSource | undefined;
let eventJobId: string | null = null;

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
      serverError.value = '세션 종료됨';
      return;
    }
    health.value = null;
    serverError.value = readableError(error, '웹 서버에 연결할 수 없습니다. 서버 실행 상태를 확인하세요.');
  }
}

async function submitJob() {
  if (submitting.value || isActiveJob.value) return;
  const normalized = prompt.value.trim();
  if (!normalized) {
    showToast('만들 앱의 목적과 핵심 기능을 입력해 주세요.');
    return;
  }
  if (!health.value?.ready) {
    showToast('먼저 외부 LLM 연결을 설정해 주세요.');
    return;
  }

  submitting.value = true;
  try {
    const created = await createJob(normalized, { mode: runMode.value });
    job.value = created;
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
        showToast('이미 실행 중인 작업 상태로 연결했습니다.');
      } else {
        job.value = makeRequestErrorJob(error.payload, normalized);
        showToast(error.payload.title || '요청을 시작하지 못했습니다.');
      }
    } else {
      job.value = makeRequestErrorJob(
        {
          code: 'REQUEST_FAILED',
          title: '요청을 시작하지 못했습니다',
          message: readableError(error, '요청 처리 중 오류가 발생했습니다.'),
          action: '입력과 서버 상태를 확인한 뒤 다시 시도하세요.',
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
  eventSource = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);

  const refreshFromEvent = (event: MessageEvent) => {
    try {
      const payload = JSON.parse(event.data || '{}');
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
    'job_completed',
    'job_failed',
    'job_cancelled',
    'preview_built',
    'llm_text',
    'tool_call',
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
      serverError.value = '세션 종료됨';
      return;
    }
    if (error instanceof ApiError && error.status === 404) {
      setCurrentJob(null);
      job.value = null;
      showToast('이전에 보던 작업 기록을 찾을 수 없어 초기화했습니다.');
      return;
    }
    serverError.value = readableError(error, '상태를 불러오지 못했습니다. 잠시 뒤 다시 시도합니다.');
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
    showToast('현재 작업을 취소했습니다.');
  } catch (error) {
    showToast(readableError(error, '작업 취소에 실패했습니다.'));
    schedulePoll(1200);
  } finally {
    cancelling.value = false;
  }
}

function handleJobUpdated(updated: JobPayload) {
  job.value = updated;
  if (!updated.terminal && updated.id !== 'request-error') {
    setCurrentJob(updated.id);
    schedulePoll(350);
  }
}

async function endCurrentSession() {
  if (endingSession.value) return;
  endingSession.value = true;
  window.clearTimeout(pollTimer);
  setCurrentJob(null);
  job.value = null;
  prompt.value = '';
  health.value = null;
  serverError.value = '세션 종료됨';
  try {
    const payload = await endSession();
    showToast(payload.message || '세션을 종료합니다.');
    serverError.value = '세션 종료됨';
    health.value = null;
  } catch (error) {
    endingSession.value = false;
    showToast(readableError(error, '세션 종료에 실패했습니다.'));
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
    status_label: '요청 오류',
    message: error.message || '요청 처리 중 오류가 발생했습니다.',
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
  <main class="app-shell">
    <TopBar
      :health="health"
      :server-error="serverError"
      :can-cancel="isActiveJob"
      :cancelling="cancelling"
      :ending-session="endingSession"
      @refresh="refreshHealth"
      @open-settings="openProviderSettings"
      @cancel="cancelActiveJob"
      @end-session="endCurrentSession"
    />

    <section class="hero" aria-labelledby="heroTitle">
      <div>
        <p class="eyebrow">AUTONOMOUS AI APP BUILDER</p>
        <h1 id="heroTitle">프롬프트 하나로<br />앱 기획부터 ZIP까지.</h1>
        <p class="hero-copy">
          원하는 앱을 설명하면 계획형 AI 에이전트 파이프라인이 요구사항을 정리하고,
          코드를 작성하고, 테스트와 빌드를 실행한 뒤 프리뷰와 소스 ZIP으로 결과를 제공합니다.
        </p>
      </div>
      <div class="hero-card" aria-label="v6 핵심 흐름">
        <span>01</span>
        <strong>앱 설명</strong>
        <span>02</span>
        <strong>AI 에이전트 제작</strong>
        <span>03</span>
        <strong>검증·프리뷰·ZIP</strong>
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
        @update:mode="runMode = $event"
        @submit="submitJob"
      />
      <JobPanel :job="job" @job-updated="handleJobUpdated" @new-request="startNewRequest" @toast="showToast" />
    </div>

    <footer class="footer-note">
      <span aria-hidden="true">●</span>
      <p>
        로컬 작업공간에서 실행됩니다. 백엔드 파이프라인 로직은 동일하게 유지하며,
        배포와 운영 데이터 변경은 자동으로 수행하지 않습니다.
      </p>
    </footer>
  </main>
  <ProviderSettings
    v-if="settingsOpen"
    @close="closeProviderSettings"
    @toast="showToast"
    @changed="handleProviderSettingsChanged"
  />
  <Toast :message="toastMessage" />
</template>
