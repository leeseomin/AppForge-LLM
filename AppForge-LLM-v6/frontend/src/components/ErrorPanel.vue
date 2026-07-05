<script setup lang="ts">
import { computed } from 'vue';
import type { ApiErrorPayload } from '../types';

const props = defineProps<{
  error: ApiErrorPayload | null;
  canRetry?: boolean;
}>();

const emit = defineEmits<{
  copied: [message: string];
  retry: [stage?: string | null];
}>();

const technicalText = computed(() => {
  const technical = props.error?.technical || {};
  return Object.keys(technical).length
    ? JSON.stringify(technical, null, 2)
    : '추가 기술 정보가 없습니다.';
});

const hasTechnical = computed(() => {
  const technical = props.error?.technical || {};
  return Object.keys(technical).length > 0;
});

async function copyErrorDetails() {
  if (!props.error) return;
  const text = [
    props.error.code,
    props.error.title,
    props.error.message,
    props.error.action,
    technicalText.value,
  ]
    .filter(Boolean)
    .join('\n\n');

  try {
    await navigator.clipboard.writeText(text);
    emit('copied', '오류 세부정보를 복사했습니다.');
  } catch (_error) {
    emit('copied', '클립보드 권한이 없어 복사하지 못했습니다. 기술 세부정보를 직접 선택해 주세요.');
  }
}
</script>

<template>
  <section v-if="props.error" class="error-panel" aria-labelledby="errorTitle" aria-live="assertive">
    <div class="error-heading">
      <span class="error-symbol" aria-hidden="true">!</span>
      <div>
        <div class="error-meta">
          <span>{{ props.error.code || 'ERROR' }}</span>
          <span v-if="props.error.stage">· {{ props.error.stage }}</span>
          <span v-if="props.error.attempt">· {{ props.error.attempt }}번째 시도</span>
        </div>
        <h3 id="errorTitle">{{ props.error.title || '작업을 완료하지 못했습니다' }}</h3>
      </div>
    </div>
    <p class="error-message">{{ props.error.message || '오류 메시지가 제공되지 않았습니다.' }}</p>
    <div v-if="props.error.action" class="error-action">
      <strong>해결 방법</strong>
      <p>{{ props.error.action }}</p>
    </div>
    <div v-if="props.canRetry" class="error-actions">
      <button class="primary-button compact" type="button" @click="emit('retry', props.error.stage)">
        이 스테이지부터 재시도
      </button>
      <button class="secondary-button" type="button" @click="emit('retry', props.error.stage)">
        자동 수리 시도
      </button>
    </div>
    <details v-if="hasTechnical" class="technical-details">
      <summary>기술 세부정보 보기</summary>
      <div class="technical-toolbar">
        <span>에이전트 출력·검증 실패·명령 정보</span>
        <button type="button" @click="copyErrorDetails">복사</button>
      </div>
      <pre tabindex="0">{{ technicalText }}</pre>
    </details>
  </section>
</template>
