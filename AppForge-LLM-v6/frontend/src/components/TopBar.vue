<script setup lang="ts">
import type { HealthPayload } from '../types';

const props = defineProps<{
  health: HealthPayload | null;
  serverError: string;
  canCancel: boolean;
  cancelling: boolean;
  endingSession: boolean;
}>();

const emit = defineEmits<{
  refresh: [];
  openSettings: [];
  cancel: [];
  endSession: [];
}>();
</script>

<template>
  <header class="topbar" aria-label="앱 헤더">
    <a class="brand" href="/" aria-label="AppForge-LLM v6 홈">
      <span class="brand-mark" aria-hidden="true">AF</span>
      <span class="brand-copy">
        <strong>AppForge</strong>
        <small>AI App Builder</small>
      </span>
    </a>

    <div class="topbar-actions">
      <button class="ghost-button" type="button" @click="emit('openSettings')">LLM 연결</button>
      <button
        v-if="props.canCancel"
        class="ghost-button danger"
        type="button"
        :disabled="props.cancelling"
        @click="emit('cancel')"
      >
        {{ props.cancelling ? '취소 중' : '취소' }}
      </button>
      <button class="ghost-button" type="button" @click="emit('refresh')">
        상태 새로고침
      </button>
      <button
        class="ghost-button danger"
        type="button"
        :disabled="props.endingSession"
        @click="emit('endSession')"
      >
        {{ props.endingSession ? '종료 중' : '세션 종료' }}
      </button>
      <div
        class="server-badge"
        :class="{
          'is-checking': !props.health && !props.serverError,
          'is-ready': props.health?.ready,
          'is-error': props.serverError || props.health?.ready === false,
        }"
        role="status"
        aria-live="polite"
      >
        <span class="server-dot" aria-hidden="true"></span>
        <span v-if="props.health?.ready">{{ props.health.driver.label }} 준비됨</span>
        <span v-else-if="props.serverError === '세션 종료됨'">세션 종료됨</span>
        <span v-else-if="props.serverError">서버 연결 오류</span>
        <span v-else-if="props.health">실행기 설정 필요</span>
        <span v-else>실행 환경 확인 중</span>
      </div>
    </div>
  </header>
</template>
