<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';

const props = defineProps<{
  modelValue: string;
  promptMaxChars: number;
  ready: boolean;
  busy: boolean;
  submitting: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
  submit: [];
}>();

const promptInput = ref<HTMLTextAreaElement | null>(null);
const isInvalid = ref(false);

const examples = [
  '개인 지출 CSV를 불러와 월별 예산과 카테고리별 사용량을 보여주는 반응형 웹앱',
  '동네 독서모임 일정, 참석 여부, 책 메모를 관리하는 모바일 친화 웹앱',
  '소규모 팀이 고객 문의를 칸반으로 분류하고 SLA를 추적하는 내부 도구',
];

const countLabel = computed(() => {
  return `${props.modelValue.length.toLocaleString('ko-KR')} / ${props.promptMaxChars.toLocaleString('ko-KR')}`;
});

const buttonLabel = computed(() => {
  if (props.submitting) return '시작하는 중…';
  if (props.busy) return '앱 생성 중…';
  return '앱 만들기';
});

const disabled = computed(() => props.submitting || props.busy || !props.ready);

function updatePrompt(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value;
  emit('update:modelValue', value);
  isInvalid.value = false;
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
          <p class="section-kicker">ONE REQUEST → VERIFIED SOURCE</p>
          <label id="requestTitle" for="promptInput">어떤 앱을 만들까요?</label>
          <p>사용자, 핵심 기능, 데이터, 원하는 실행 환경을 함께 적으면 결과가 더 정확해집니다.</p>
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
        placeholder="예: 개인 지출 CSV를 불러와 월별 예산과 카테고리별 사용량을 보여주는 반응형 웹앱을 만들어 주세요. 로컬 저장, 빈 상태와 오류 상태, 테스트, Docker 실행 방법을 포함해 주세요."
        :aria-invalid="isInvalid ? 'true' : 'false'"
        aria-describedby="requestHelp readinessNotice"
        :disabled="props.busy"
        @input="updatePrompt"
        @keydown="onKeydown"
      ></textarea>

      <p v-if="isInvalid" class="inline-error">만들 앱의 목적과 핵심 기능을 입력해 주세요.</p>

      <div class="prompt-chips" aria-label="예시 요청">
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
          <kbd>Ctrl</kbd><span>+</span><kbd>Enter</kbd>로 바로 시작
        </p>
        <button class="primary-button" type="submit" :disabled="disabled">
          <span>{{ buttonLabel }}</span>
          <span class="button-arrow" aria-hidden="true">→</span>
        </button>
      </div>
    </form>
  </section>
</template>
