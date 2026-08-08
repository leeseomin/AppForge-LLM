<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from '../i18n';
import type { ApiErrorPayload } from '../types';

const { t } = useI18n();

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
    : t('error.noTechnical');
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
    emit('copied', t('error.copied'));
  } catch (_error) {
    emit('copied', t('error.copyFailed'));
  }
}
</script>

<template>
  <section v-if="props.error" class="error-panel" aria-labelledby="errorTitle" aria-live="assertive">
    <div class="error-heading">
      <span class="error-symbol" aria-hidden="true">!</span>
      <div>
        <div v-if="props.error.stage_label || props.error.attempt" class="error-meta">
          <span v-if="props.error.stage_label">{{ props.error.stage_label }}</span>
          <span v-if="props.error.attempt">· {{ t('error.attempt', { attempt: props.error.attempt }) }}</span>
        </div>
        <h3 id="errorTitle">{{ props.error.title || t('error.title') }}</h3>
      </div>
    </div>
    <p class="error-message">{{ props.error.message || t('error.noMessage') }}</p>
    <div v-if="props.error.action" class="error-action">
      <strong>{{ t('error.solution') }}</strong>
      <p>{{ props.error.action }}</p>
    </div>
    <div v-if="props.canRetry" class="error-actions">
      <button class="primary-button compact" type="button" @click="emit('retry', props.error.stage)">
        {{ t('error.retryStage') }}
      </button>
      <button class="secondary-button" type="button" @click="emit('retry', props.error.stage)">
        {{ t('error.autoRepair') }}
      </button>
    </div>
    <details v-if="hasTechnical" class="technical-details">
      <summary>{{ t('error.details') }}</summary>
      <div class="technical-toolbar">
        <span>{{ t('error.detailsLabel') }}</span>
        <button type="button" @click="copyErrorDetails">{{ t('common.copy') }}</button>
      </div>
      <pre tabindex="0">{{ technicalText }}</pre>
    </details>
  </section>
</template>
