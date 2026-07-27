<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { getArtifact, listArtifacts } from '../api';
import type { ArtifactSummary } from '../types';

const props = defineProps<{
  jobId: string;
  initialArtifact?: string;
}>();

const emit = defineEmits<{
  toast: [message: string];
}>();

const artifacts = ref<ArtifactSummary[]>([]);
const selected = ref('');
const payload = ref<unknown>(null);
const loading = ref(false);
const payloadLoading = ref(false);
const error = ref('');

const prettyPayload = computed(() => (payload.value == null ? '' : JSON.stringify(payload.value, null, 2)));

async function loadList() {
  loading.value = true;
  error.value = '';
  try {
    const result = await listArtifacts(props.jobId);
    artifacts.value = result.artifacts;
    const preferred = props.initialArtifact
      ? result.artifacts.find((artifact) => artifact.name === props.initialArtifact)
      : null;
    const next = preferred || (!selected.value ? result.artifacts[0] : null);
    if (next) {
      await openArtifact(next.name);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '아티팩트 목록을 불러오지 못했습니다.';
  } finally {
    loading.value = false;
  }
}

async function openArtifact(name: string) {
  selected.value = name;
  payloadLoading.value = true;
  try {
    const result = await getArtifact(props.jobId, name);
    payload.value = result.payload;
  } catch (err) {
    emit('toast', err instanceof Error ? err.message : '아티팩트를 열 수 없습니다.');
  } finally {
    payloadLoading.value = false;
  }
}

onMounted(loadList);
watch(() => props.jobId, () => {
  selected.value = '';
  payload.value = null;
  loadList();
});

watch(() => props.initialArtifact, async (name) => {
  if (!name || selected.value === name) return;
  if (!artifacts.value.some((artifact) => artifact.name === name)) {
    await loadList();
    return;
  }
  await openArtifact(name);
});
</script>

<template>
  <section class="artifact-browser" aria-labelledby="artifactBrowserTitle">
    <div class="panel-heading compact">
      <div>
        <h3 id="artifactBrowserTitle">중간 산출물</h3>
        <p>요구사항, 설계, 검증 결과를 JSON 원문으로 확인합니다.</p>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadList">
        {{ loading ? '로딩 중' : '새로고침' }}
      </button>
    </div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div v-if="artifacts.length" class="artifact-grid">
      <aside class="artifact-list" aria-label="아티팩트 목록">
        <button
          v-for="artifact in artifacts"
          :key="artifact.name"
          type="button"
          :class="{ selected: selected === artifact.name }"
          @click="openArtifact(artifact.name)"
        >
          <span>{{ artifact.name }}</span>
          <small>{{ artifact.summary }}</small>
        </button>
      </aside>
      <article class="artifact-viewer">
        <div class="code-toolbar">
          <strong>{{ selected || '아티팩트' }}</strong>
          <span v-if="payloadLoading">불러오는 중…</span>
        </div>
        <pre v-if="prettyPayload" tabindex="0"><code>{{ prettyPayload }}</code></pre>
      </article>
    </div>
    <p v-else-if="!loading" class="empty-note">아직 표시할 산출물이 없습니다.</p>
  </section>
</template>
