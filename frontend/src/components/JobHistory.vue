<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { listJobs, rerunJob, updateJobMetadata } from '../api';
import { useI18n } from '../i18n';
import type { JobPayload, JobSummary } from '../types';

const { locale, t } = useI18n();

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
  return new Intl.DateTimeFormat(locale.value === 'ko' ? 'ko-KR' : 'en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function modelLabel(job: JobSummary): string {
  if (!job.llm?.provider && !job.llm?.model) return t('history.previousDefaultModel');
  return [job.llm.provider, job.llm.model].filter(Boolean).join(' / ');
}

function tokenLabel(job: JobSummary): string {
  const total = job.usage?.total_tokens;
  return Number.isFinite(total)
    ? t('common.tokens', { value: Number(total).toLocaleString(locale.value === 'ko' ? 'ko-KR' : 'en-US') })
    : '';
}

function statusLabel(job: JobSummary): string {
  if (job.status === 'queued') return t('stage.queued');
  if (job.status === 'initializing') return t('stage.initializing');
  if (job.status === 'running') return t('stage.running');
  if (job.status === 'packaging') return t('stage.packaging');
  if (job.status === 'awaiting_approval') return t('stage.awaitingApproval');
  if (job.status === 'completed') return t('stage.completed');
  if (job.status === 'failed') return t('stage.failed');
  if (job.status === 'cancelled') return t('stage.cancelled');
  return job.status_label || job.status;
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
    error.value = reason instanceof Error ? reason.message : t('history.loadError');
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
    emit('toast', reason instanceof Error ? reason.message : t('history.starError'));
  } finally {
    actionId.value = '';
  }
}

async function toggleArchive(job: JobSummary): Promise<void> {
  actionId.value = job.id;
  try {
    await updateJobMetadata(job.id, { archived: !job.archived });
    jobs.value = jobs.value.filter((item) => item.id !== job.id);
    emit('toast', job.archived ? t('history.restored') : t('history.archivedToast'));
  } catch (reason) {
    emit('toast', reason instanceof Error ? reason.message : t('history.archiveError'));
  } finally {
    actionId.value = '';
  }
}

async function runAgain(job: JobSummary): Promise<void> {
  if (props.busy) {
    emit('toast', t('history.busyRerun'));
    return;
  }
  actionId.value = job.id;
  try {
    const created = await rerunJob(job.id);
    emit('rerun', created);
    emit('toast', t('history.rerunStarted'));
  } catch (reason) {
    emit('toast', reason instanceof Error ? reason.message : t('history.rerunError'));
  } finally {
    actionId.value = '';
  }
}

function selectJob(job: JobSummary): void {
  if (props.busy && job.id !== props.currentJobId) {
    emit('toast', t('history.openBusy'));
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
          <p class="section-kicker">{{ t('history.kicker') }}</p>
          <h2 id="historyTitle">{{ t('history.title') }}</h2>
          <p>{{ t('history.help') }}</p>
        </div>
        <button class="icon-button" type="button" :aria-label="t('history.closeLabel')" @click="emit('close')">×</button>
      </header>

      <div class="history-tabs" role="tablist" :aria-label="t('history.tabsLabel')">
        <button type="button" role="tab" :aria-selected="!archived" @click="switchArchive(false)">{{ t('history.jobs') }}</button>
        <button type="button" role="tab" :aria-selected="archived" @click="switchArchive(true)">{{ t('history.archived') }}</button>
      </div>

      <div class="history-body">
        <p v-if="loading" class="history-state">{{ t('history.loading') }}</p>
        <div v-else-if="error" class="history-state is-error">
          <p>{{ error }}</p>
          <button class="secondary-button" type="button" @click="load(true)">{{ t('common.retry') }}</button>
        </div>
        <p v-else-if="jobs.length === 0" class="history-state">
          {{ archived ? t('history.emptyArchived') : t('history.emptySaved') }}
        </p>
        <ul v-else class="history-list">
          <li v-for="item in jobs" :key="item.id" :class="{ 'is-current': item.id === props.currentJobId }">
            <button class="history-main" type="button" @click="selectJob(item)">
              <span class="history-prompt">{{ item.prompt }}</span>
              <span class="history-meta">
                <span>{{ statusLabel(item) }}</span>
                <span>{{ formatDate(item.updated_at) }}</span>
                <span>{{ modelLabel(item) }}</span>
                <span v-if="tokenLabel(item)">{{ tokenLabel(item) }}</span>
              </span>
            </button>
            <div class="history-actions">
              <button
                type="button"
                :disabled="actionId === item.id"
                :aria-label="item.starred ? t('history.starRemove') : t('history.starAdd')"
                :title="item.starred ? t('history.starRemove') : t('history.starAdd')"
                @click="toggleStar(item)"
              >
                {{ item.starred ? '★' : '☆' }}
              </button>
              <button type="button" :disabled="actionId === item.id || props.busy" @click="runAgain(item)">
                {{ t('history.rerun') }}
              </button>
              <button
                type="button"
                :disabled="actionId === item.id || item.id === props.currentJobId"
                @click="toggleArchive(item)"
              >
                {{ archived ? t('history.restore') : t('history.archive') }}
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
          {{ loadingMore ? t('common.loadingEllipsis') : t('history.loadMore') }}
        </button>
      </div>
    </section>
  </div>
</template>
