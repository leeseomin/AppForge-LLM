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
  openHistory: [];
  openSettings: [];
  cancel: [];
  endSession: [];
}>();
</script>

<template>
  <header class="topbar" aria-label="앱 헤더">
    <a class="brand" href="/" aria-label="AppForge-LLM v7 홈">
      <span class="brand-mark" aria-hidden="true">AF</span>
      <span class="brand-copy">
        <strong>AppForge</strong>
        <small>Local AI App Builder</small>
      </span>
    </a>

    <nav class="topbar-actions" aria-label="작업 메뉴">
      <p class="sidebar-label">Workspace</p>
      <button class="ghost-button" type="button" @click="emit('openHistory')">
        <span>작업 기록</span><span aria-hidden="true">↗</span>
      </button>
      <button class="ghost-button is-accent" type="button" @click="emit('openSettings')">
        <span>LLM 연결</span><span aria-hidden="true">＋</span>
      </button>
      <button
        v-if="props.canCancel"
        class="ghost-button danger"
        type="button"
        :disabled="props.cancelling"
        @click="emit('cancel')"
      >
        <span>{{ props.cancelling ? '취소 중' : '현재 작업 취소' }}</span><span aria-hidden="true">×</span>
      </button>
      <button class="ghost-button" type="button" @click="emit('refresh')">
        <span>상태 새로고침</span><span aria-hidden="true">↻</span>
      </button>
      <button
        class="ghost-button danger"
        type="button"
        :disabled="props.endingSession"
        @click="emit('endSession')"
      >
        <span>{{ props.endingSession ? '종료 중' : '세션 종료' }}</span><span aria-hidden="true">⏻</span>
      </button>
    </nav>

    <div class="sidebar-status">
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
      <p>AppForge v7 · 로컬 우선 실행 환경</p>
    </div>
  </header>
</template>
