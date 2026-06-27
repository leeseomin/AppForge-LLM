<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  value: number;
}>();

const normalized = computed(() => Math.min(100, Math.max(0, Math.round(props.value || 0))));
const background = computed(() => `conic-gradient(var(--accent-strong) ${normalized.value * 3.6}deg, rgba(255,255,255,.1) 0deg)`);
</script>

<template>
  <div class="progress-ring" :style="{ background }" aria-hidden="true">
    <strong>{{ normalized }}</strong>
    <span>%</span>
  </div>
</template>
