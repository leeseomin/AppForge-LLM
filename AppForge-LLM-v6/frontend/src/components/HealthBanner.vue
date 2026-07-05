<script setup lang="ts">
import { computed } from 'vue';
import type { HealthPayload } from '../types';

const props = defineProps<{
  health: HealthPayload | null;
  serverError: string;
}>();

const bannerText = computed(() => {
  if (props.serverError) {
    return props.serverError;
  }
  if (!props.health) {
    return '외부 LLM 연결 환경을 확인하고 있습니다.';
  }
  if (!props.health.ready) {
    return `${props.health.driver.message} ${props.health.driver.action}`.trim();
  }
  const networkText = props.health.network_enabled ? '패키지 설치용 네트워크 허용' : '네트워크 사용 안 함';
  const destructiveText = props.health.safety.destructive_operations_enabled
    ? '파괴 작업 허용됨'
    : '배포/파괴 작업 차단';
  return `${props.health.driver.message} · ${networkText} · ${destructiveText}`;
});
</script>

<template>
  <section
    class="health-banner"
    :class="{
      'is-ready': props.health?.ready,
      'is-error': props.serverError || props.health?.ready === false,
    }"
    role="status"
    aria-live="polite"
  >
    <span aria-hidden="true">{{ props.health?.ready ? '✓' : props.serverError || props.health ? '!' : '…' }}</span>
    <p>{{ bannerText }}</p>
  </section>
</template>
