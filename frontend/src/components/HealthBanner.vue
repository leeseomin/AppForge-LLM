<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from '../i18n';
import type { HealthPayload } from '../types';

const { locale, t } = useI18n();

const props = defineProps<{
  health: HealthPayload | null;
  serverError: string;
}>();

const bannerText = computed(() => {
  if (props.serverError) {
    return props.serverError;
  }
  if (!props.health) {
    return t('health.checking');
  }
  if (!props.health.ready) {
    return locale.value === 'ko'
      ? `${props.health.driver.message} ${props.health.driver.action}`.trim()
      : t('health.notReady');
  }
  const networkText = props.health.network_enabled
    ? t('health.networkAllowed')
    : props.health.safety.dependency_install_enabled
      ? t('health.dependenciesOnly')
      : t('health.noNetwork');
  const destructiveText = props.health.safety.destructive_operations_enabled
    ? t('health.destructiveAllowed')
    : t('health.destructiveBlocked');
  const message = locale.value === 'ko'
    ? props.health.driver.message
    : t('topbar.ready', { label: props.health.driver.label });
  return t('health.readySummary', { message, network: networkText, safety: destructiveText });
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
