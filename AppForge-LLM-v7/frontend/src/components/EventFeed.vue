<script setup lang="ts">
import { computed } from 'vue';
import type { JobEvent } from '../types';

const props = defineProps<{
  events: JobEvent[];
}>();

const recentEvents = computed(() => [...props.events].slice(-6).reverse());

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
      <li v-for="event in recentEvents" :key="`${event.timestamp}-${event.event}-${event.message}`">
        <time :datetime="event.timestamp">{{ formatTime(event.timestamp) }}</time>
        <span>{{ event.message || event.event }}</span>
      </li>
    </ul>
  </aside>
</template>
