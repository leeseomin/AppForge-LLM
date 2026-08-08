<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue';
import type { JobEvent } from '../types';

const MarkdownContent = defineAsyncComponent(() => import('./MarkdownContent.vue'));

const props = defineProps<{
  events: JobEvent[];
}>();

const recentEvents = computed(() => [...props.events].slice(-12).reverse());

function markdownFor(event: JobEvent): string {
  const value = event.data?.markdown;
  return typeof value === 'string' ? value : '';
}

function usageFor(event: JobEvent): { total_tokens?: number; estimated_cost_usd?: number } | null {
  const value = event.data?.usage;
  return value && typeof value === 'object'
    ? value as { total_tokens?: number; estimated_cost_usd?: number }
    : null;
}

function formatTokens(value?: number): string {
  return Number.isFinite(value) ? Number(value).toLocaleString('ko-KR') : '-';
}

function formatCost(value?: number): string {
  return Number.isFinite(value) ? `$${Number(value).toFixed(6)}` : '';
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}
</script>

<template>
  <aside v-if="recentEvents.length" class="event-feed" aria-labelledby="eventFeedTitle">
    <h3 id="eventFeedTitle">최근 이벤트</h3>
    <ul>
      <li
        v-for="event in recentEvents"
        :key="event.id || `${event.timestamp}-${event.event}-${event.message}`"
        :class="{ 'has-markdown': Boolean(markdownFor(event)) }"
      >
        <div class="event-meta">
          <time :datetime="event.timestamp">{{ formatTime(event.timestamp) }}</time>
          <strong>{{ event.message || event.event }}</strong>
        </div>
        <MarkdownContent v-if="markdownFor(event)" :content="markdownFor(event)" />
        <span v-else-if="usageFor(event)" class="event-usage">
          {{ formatTokens(usageFor(event)?.total_tokens) }} tokens
          <template v-if="formatCost(usageFor(event)?.estimated_cost_usd)">
            · {{ formatCost(usageFor(event)?.estimated_cost_usd) }} 추정
          </template>
        </span>
      </li>
    </ul>
  </aside>
</template>
