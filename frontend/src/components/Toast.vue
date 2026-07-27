<script setup lang="ts">
const props = defineProps<{
  message: string;
}>();
</script>

<template>
  <Transition name="toast-fade">
    <div v-if="props.message" class="toast" role="status" aria-live="polite">{{ props.message }}</div>
  </Transition>
</template>
