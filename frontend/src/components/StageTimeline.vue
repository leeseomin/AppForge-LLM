<script setup lang="ts">
import type { JobStage } from '../types';

const props = defineProps<{
  stages: JobStage[];
  activeStage: string | null;
}>();

const emit = defineEmits<{
  openArtifact: [name: string];
}>();

const statusLabels: Record<string, string> = {
  pending: '대기',
  running: '실행 중',
  validating: '검증 중',
  retrying: '자동 재시도',
  awaiting_approval: '승인 대기',
  completed: '완료',
  failed: '실패',
};

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
  return Number.isFinite(value) ? `${Number(value).toLocaleString('ko-KR')} tokens` : '';
}

function formatCost(value?: number) {
  return Number.isFinite(value) ? `$${Number(value).toFixed(6)}` : '';
}
</script>

<template>
  <ol class="stage-list" aria-label="진행 단계">
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
          <span v-if="stage.kind === 'system'" class="stage-kind">SYSTEM</span>
          <span v-else-if="stage.approval || stage.approval_required" class="stage-kind">APPROVAL</span>
        </div>
        <p class="stage-detail">{{ stage.detail || stage.description || '대기 중' }}</p>
        <p v-if="stage.error?.message" class="stage-error">{{ stage.error.message }}</p>
        <p v-if="stage.usage?.total_tokens" class="stage-usage">
          {{ formatTokens(stage.usage.total_tokens) }}
          <template v-if="formatCost(stage.usage.estimated_cost_usd)">
            · {{ formatCost(stage.usage.estimated_cost_usd) }} 추정
          </template>
        </p>
        <div v-if="stageArtifacts(stage).length" class="stage-artifacts" aria-label="단계 산출물">
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
        <strong>{{ statusLabels[stage.status] || stage.status || '대기' }}</strong>
        <span v-if="stage.attempt > 1">{{ stage.attempt }}번째 시도</span>
      </div>
    </li>
  </ol>
</template>
