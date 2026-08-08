<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { listJobs, rerunJob, updateJobMetadata } from '../api';
import type { JobPayload, JobSummary } from '../types';

const props = defineProps<{
  currentJobId: string | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  close: [];
  select: [jobId: string];
  rerun: [job: JobPayload];
  toast: [message: string];
}>();

const jobs = ref<JobSummary[]>([]);
const archived = ref(false);
const cursor = ref<string | null>(null);
const hasMore = ref(false);
const loading = ref(false);
const loadingMore = ref(false);
const actionId = ref('');
const error = ref('');

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function modelLabel(job: JobSummary): string {
  if (!job.llm?.provider && !job.llm?.model) return '이전 기본 모델';
  return [job.llm.provider, job.llm.model].filter(Boolean).join(' / ');
}

function tokenLabel(job: JobSummary): string {
  const total = job.usage?.total_tokens;
  return Number.isFinite(total) ? `${Number(total).toLocaleString('ko-KR')} tokens` : '';
}

async function load(reset = true): Promise<void> {
  if (reset) {
    loading.value = true;
    cursor.value = null;
    jobs.value = [];
  } else {
    loadingMore.value = true;
  }
  error.value = '';
  try {
    const payload = await listJobs({
      limit: 20,
      cursor: reset ? null : cursor.value,
      archived: archived.value,
    });
    jobs.value = reset ? payload.jobs : [...jobs.value, ...payload.jobs];
    cursor.value = payload.next_cursor;
    hasMore.value = payload.has_more;
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '작업 기록을 불러오지 못했습니다.';
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

async function switchArchive(value: boolean): Promise<void> {
  if (archived.value === value) return;
  archived.value = value;
  await load(true);
}

async function toggleStar(job: JobSummary): Promise<void> {
  actionId.value = job.id;
  try {
    const updated = await updateJobMetadata(job.id, { starred: !job.starred });
    job.starred = Boolean(updated.starred);
    jobs.value = [...jobs.value].sort((left, right) => {
      if (left.starred !== right.starred) return left.starred ? -1 : 1;
      return right.updated_at.localeCompare(left.updated_at);
    });
  } catch (reason) {
    emit('toast', reason instanceof Error ? reason.message : '별표 상태를 바꾸지 못했습니다.');
  } finally {
    actionId.value = '';
  }
}

async function toggleArchive(job: JobSummary): Promise<void> {
  actionId.value = job.id;
  try {
    await updateJobMetadata(job.id, { archived: !job.archived });
    jobs.value = jobs.value.filter((item) => item.id !== job.id);
    emit('toast', job.archived ? '작업을 목록으로 복원했습니다.' : '작업을 보관했습니다.');
  } catch (reason) {
    emit('toast', reason instanceof Error ? reason.message : '보관 상태를 바꾸지 못했습니다.');
  } finally {
    actionId.value = '';
  }
}

async function runAgain(job: JobSummary): Promise<void> {
  if (props.busy) {
    emit('toast', '현재 작업이 끝난 뒤 과거 작업을 재실행할 수 있습니다.');
    return;
  }
  actionId.value = job.id;
  try {
    const created = await rerunJob(job.id);
    emit('rerun', created);
    emit('toast', '같은 설정으로 작업을 다시 시작했습니다.');
  } catch (reason) {
    emit('toast', reason instanceof Error ? reason.message : '작업을 재실행하지 못했습니다.');
  } finally {
    actionId.value = '';
  }
}

function selectJob(job: JobSummary): void {
  if (props.busy && job.id !== props.currentJobId) {
    emit('toast', '진행 중인 작업이 있어 다른 기록은 완료 후 열 수 있습니다.');
    return;
  }
  emit('select', job.id);
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close');
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown);
  void load(true);
});

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));
</script>

<template>
  <div class="modal-backdrop history-backdrop" @click.self="emit('close')">
    <section class="history-panel" role="dialog" aria-modal="true" aria-labelledby="historyTitle">
      <header class="history-header">
        <div>
          <p class="section-kicker">DURABLE LOCAL JOBS</p>
          <h2 id="historyTitle">작업 히스토리</h2>
          <p>과거 작업을 열거나, 별표·보관으로 정리하고, 같은 설정으로 다시 실행합니다.</p>
        </div>
        <button class="icon-button" type="button" aria-label="작업 히스토리 닫기" @click="emit('close')">×</button>
      </header>

      <div class="history-tabs" role="tablist" aria-label="작업 기록 범위">
        <button type="button" role="tab" :aria-selected="!archived" @click="switchArchive(false)">작업</button>
        <button type="button" role="tab" :aria-selected="archived" @click="switchArchive(true)">보관됨</button>
      </div>

      <div class="history-body">
        <p v-if="loading" class="history-state">작업 기록을 불러오는 중…</p>
        <div v-else-if="error" class="history-state is-error">
          <p>{{ error }}</p>
          <button class="secondary-button" type="button" @click="load(true)">다시 시도</button>
        </div>
        <p v-else-if="jobs.length === 0" class="history-state">
          {{ archived ? '보관된 작업이 없습니다.' : '아직 저장된 작업이 없습니다.' }}
        </p>
        <ul v-else class="history-list">
          <li v-for="item in jobs" :key="item.id" :class="{ 'is-current': item.id === props.currentJobId }">
            <button class="history-main" type="button" @click="selectJob(item)">
              <span class="history-prompt">{{ item.prompt }}</span>
              <span class="history-meta">
                <span>{{ item.status_label || item.status }}</span>
                <span>{{ formatDate(item.updated_at) }}</span>
                <span>{{ modelLabel(item) }}</span>
                <span v-if="tokenLabel(item)">{{ tokenLabel(item) }}</span>
              </span>
            </button>
            <div class="history-actions">
              <button
                type="button"
                :disabled="actionId === item.id"
                :aria-label="item.starred ? '별표 해제' : '별표 추가'"
                :title="item.starred ? '별표 해제' : '별표 추가'"
                @click="toggleStar(item)"
              >
                {{ item.starred ? '★' : '☆' }}
              </button>
              <button type="button" :disabled="actionId === item.id || props.busy" @click="runAgain(item)">
                재실행
              </button>
              <button
                type="button"
                :disabled="actionId === item.id || item.id === props.currentJobId"
                @click="toggleArchive(item)"
              >
                {{ archived ? '복원' : '보관' }}
              </button>
            </div>
          </li>
        </ul>
        <button
          v-if="hasMore"
          class="history-more secondary-button"
          type="button"
          :disabled="loadingMore"
          @click="load(false)"
        >
          {{ loadingMore ? '불러오는 중…' : '더 보기' }}
        </button>
      </div>
    </section>
  </div>
</template>
