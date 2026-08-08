<script setup lang="ts">
import { useI18n } from '../i18n';
import type { JobStage } from '../types';

const { locale, t } = useI18n();

const props = defineProps<{
  stages: JobStage[];
  activeStage: string | null;
}>();

const emit = defineEmits<{
  openArtifact: [name: string];
}>();

function statusLabel(status: string) {
  if (status === 'running') return t('stage.running');
  if (status === 'validating') return t('stage.validating');
  if (status === 'retrying') return t('stage.retrying');
  if (status === 'awaiting_approval') return t('stage.awaitingApproval');
  if (status === 'completed') return t('stage.completed');
  if (status === 'failed') return t('stage.failed');
  return t('stage.pending');
}

function stageIcon(stage: JobStage, index: number) {
  if (stage.status === 'completed') return '✓';
  if (stage.status === 'failed') return '!';
  if (stage.status === 'retrying') return '↻';
  if (stage.status === 'awaiting_approval') return '…';
  return String(index + 1);
}

function stageArtifacts(stage: JobStage) {
  if (stage.artifacts?.length) return stage.artifacts;
  return stage.produces || [];
}

function canOpenArtifact(stage: JobStage) {
  return ['completed', 'awaiting_approval', 'failed'].includes(stage.status);
}

function formatTokens(value?: number) {
  return Number.isFinite(value)
    ? t('common.tokens', { value: Number(value).toLocaleString(locale.value === 'ko' ? 'ko-KR' : 'en-US') })
    : '';
}

function formatCost(value?: number) {
  return Number.isFinite(value) ? `$${Number(value).toFixed(6)}` : '';
}
</script>

<template>
  <ol class="stage-list" :aria-label="t('stage.listLabel')">
    <li
      v-for="(stage, index) in props.stages"
      :key="stage.id"
      class="stage-row"
      :data-status="stage.status"
      :aria-current="stage.id === props.activeStage ? 'step' : undefined"
    >
      <span class="stage-icon" aria-hidden="true">{{ stageIcon(stage, index) }}</span>
      <div class="stage-main">
        <div class="stage-title-line">
          <span class="stage-title">{{ stage.name || stage.id }}</span>
          <span v-if="stage.kind === 'system'" class="stage-kind">{{ t('stage.system') }}</span>
          <span v-else-if="stage.approval || stage.approval_required" class="stage-kind">{{ t('stage.approval') }}</span>
        </div>
        <p class="stage-detail">{{ stage.detail || stage.description || t('stage.waiting') }}</p>
        <p v-if="stage.error?.message" class="stage-error">{{ stage.error.message }}</p>
        <p v-if="stage.usage?.total_tokens" class="stage-usage">
          {{ formatTokens(stage.usage.total_tokens) }}
          <template v-if="formatCost(stage.usage.estimated_cost_usd)">
            · {{ t('stage.estimatedCost', { cost: formatCost(stage.usage.estimated_cost_usd) }) }}
          </template>
        </p>
        <div v-if="stageArtifacts(stage).length" class="stage-artifacts" :aria-label="t('stage.artifactsLabel')">
          <button
            v-for="artifact in stageArtifacts(stage)"
            :key="artifact"
            type="button"
            :disabled="!canOpenArtifact(stage)"
            @click="emit('openArtifact', artifact)"
          >
            {{ artifact }}
          </button>
        </div>
      </div>
      <div class="stage-state">
        <strong>{{ statusLabel(stage.status) }}</strong>
        <span v-if="stage.attempt > 1">{{ t('stage.attempt', { attempt: stage.attempt }) }}</span>
      </div>
    </li>
  </ol>
</template>
