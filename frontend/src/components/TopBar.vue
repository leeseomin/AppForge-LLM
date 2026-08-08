<script setup lang="ts">
import { useI18n } from '../i18n';
import type { HealthPayload } from '../types';

const { t } = useI18n();

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
  <header class="topbar" :aria-label="t('topbar.headerLabel')">
    <a class="brand" href="/" :aria-label="t('topbar.homeLabel')">
      <span class="brand-mark" aria-hidden="true">AF</span>
      <span class="brand-copy">
        <strong>AppForge</strong>
        <small>{{ t('topbar.subtitle') }}</small>
      </span>
    </a>

    <nav class="topbar-actions" :aria-label="t('topbar.menuLabel')">
      <p class="sidebar-label">{{ t('topbar.workspace') }}</p>
      <button class="ghost-button" type="button" @click="emit('openHistory')">
        <span>{{ t('topbar.history') }}</span><span aria-hidden="true">↗</span>
      </button>
      <button class="ghost-button is-accent" type="button" @click="emit('openSettings')">
        <span>{{ t('topbar.connect') }}</span><span aria-hidden="true">＋</span>
      </button>
      <button
        v-if="props.canCancel"
        class="ghost-button danger"
        type="button"
        :disabled="props.cancelling"
        @click="emit('cancel')"
      >
        <span>{{ props.cancelling ? t('topbar.cancelling') : t('topbar.cancelJob') }}</span><span aria-hidden="true">×</span>
      </button>
      <button class="ghost-button" type="button" @click="emit('refresh')">
        <span>{{ t('topbar.refresh') }}</span><span aria-hidden="true">↻</span>
      </button>
      <button
        class="ghost-button danger"
        type="button"
        :disabled="props.endingSession"
        @click="emit('endSession')"
      >
        <span>{{ props.endingSession ? t('topbar.endingSession') : t('topbar.endSession') }}</span><span aria-hidden="true">⏻</span>
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
        <span v-if="props.health?.ready">{{ t('topbar.ready', { label: props.health.driver.label }) }}</span>
        <span v-else-if="props.endingSession">{{ t('topbar.sessionEnded') }}</span>
        <span v-else-if="props.serverError">{{ t('topbar.serverError') }}</span>
        <span v-else-if="props.health">{{ t('topbar.runnerRequired') }}</span>
        <span v-else>{{ t('topbar.checking') }}</span>
      </div>
      <p>{{ t('topbar.localFirst') }}</p>
    </div>
  </header>
</template>
