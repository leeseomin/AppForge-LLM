<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from '../i18n';
import type { JobRunSettings } from '../types';
import JobAdvancedSettings from './JobAdvancedSettings.vue';

const { locale, t } = useI18n();

const props = defineProps<{
  modelValue: string;
  promptMaxChars: number;
  ready: boolean;
  busy: boolean;
  submitting: boolean;
  mode: string;
  settings: JobRunSettings;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
  'update:mode': [value: string];
  'update:settings': [value: JobRunSettings];
  submit: [];
}>();

const promptInput = ref<HTMLTextAreaElement | null>(null);
const isInvalid = ref(false);

const examples = computed(() => [
  t('composer.example1'),
  t('composer.example2'),
  t('composer.example3'),
]);

const countLabel = computed(() => {
  const numberLocale = locale.value === 'ko' ? 'ko-KR' : 'en-US';
  return `${props.modelValue.length.toLocaleString(numberLocale)} / ${props.promptMaxChars.toLocaleString(numberLocale)}`;
});

const buttonLabel = computed(() => {
  if (props.submitting) return t('composer.starting');
  if (props.busy) return t('composer.busy');
  return t('composer.start');
});

const disabled = computed(() => props.submitting || props.busy || !props.ready);

function updatePrompt(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value;
  emit('update:modelValue', value);
  isInvalid.value = false;
}


function updateMode(value: string) {
  if (props.busy) return;
  emit('update:mode', value);
}

function submit() {
  if (!props.modelValue.trim()) {
    isInvalid.value = true;
    nextTick(() => promptInput.value?.focus());
    return;
  }
  emit('submit');
}

function applyExample(example: string) {
  if (props.busy) return;
  emit('update:modelValue', example);
  isInvalid.value = false;
  nextTick(() => promptInput.value?.focus());
}

function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    submit();
  }
}

watch(
  () => props.modelValue,
  () => {
    if (props.modelValue.trim()) {
      isInvalid.value = false;
    }
  },
);
</script>

<template>
  <section class="composer-card" aria-labelledby="requestTitle">
    <div class="card-glow" aria-hidden="true"></div>
    <form novalidate @submit.prevent="submit">
      <div class="field-heading">
        <div>
          <p class="section-kicker">{{ t('composer.kicker') }}</p>
          <label id="requestTitle" for="promptInput">{{ t('composer.title') }}</label>
          <p>{{ t('composer.help') }}</p>
        </div>
        <span class="character-count">{{ countLabel }}</span>
      </div>

      <textarea
        id="promptInput"
        ref="promptInput"
        :value="props.modelValue"
        rows="8"
        :maxlength="props.promptMaxChars"
        required
        spellcheck="true"
        autocomplete="off"
        :placeholder="t('composer.placeholder')"
        :aria-invalid="isInvalid ? 'true' : 'false'"
        aria-describedby="requestHelp readinessNotice"
        :disabled="props.busy"
        @input="updatePrompt"
        @keydown="onKeydown"
      ></textarea>

      <p v-if="isInvalid" class="inline-error">{{ t('composer.required') }}</p>

      <details class="mode-switch">
        <summary>
          <span>{{ t('composer.mode') }}</span>
          <strong>{{ props.mode === 'checkpoint' ? t('composer.checkpoint') : t('composer.autonomous') }}</strong>
        </summary>
        <fieldset :disabled="props.busy" :aria-label="t('composer.modeLabel')">
          <legend>{{ t('composer.modeLabel') }}</legend>
          <label :class="{ selected: props.mode === 'autonomous' }">
            <input
              type="radio"
              name="runMode"
              value="autonomous"
              :checked="props.mode === 'autonomous'"
              @change="updateMode('autonomous')"
            />
            <span>{{ t('composer.autonomous') }}</span>
            <small>{{ t('composer.autonomousHelp') }}</small>
          </label>
          <label :class="{ selected: props.mode === 'checkpoint' }">
            <input
              type="radio"
              name="runMode"
              value="checkpoint"
              :checked="props.mode === 'checkpoint'"
              @change="updateMode('checkpoint')"
            />
            <span>{{ t('composer.checkpoint') }}</span>
            <small>{{ t('composer.checkpointHelp') }}</small>
          </label>
        </fieldset>
      </details>

      <JobAdvancedSettings
        :settings="props.settings"
        :busy="props.busy"
        @update:settings="emit('update:settings', $event)"
      />

      <div class="prompt-chips" :aria-label="t('composer.examplesLabel')">
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          :disabled="props.busy"
          @click="applyExample(example)"
        >
          {{ example }}
        </button>
      </div>

      <div class="composer-footer">
        <p id="requestHelp" class="keyboard-hint">
          <kbd>Ctrl</kbd><span>+</span><kbd>Enter</kbd>{{ t('composer.shortcut') }}
        </p>
        <button class="primary-button" type="submit" :disabled="disabled">
          <span>{{ buttonLabel }}</span>
          <span class="button-arrow" aria-hidden="true">→</span>
        </button>
      </div>
    </form>
  </section>
</template>
