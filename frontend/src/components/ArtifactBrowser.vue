<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { getArtifact, listArtifacts } from '../api';
import { useI18n } from '../i18n';
import type { ArtifactSummary } from '../types';

const { t } = useI18n();

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
    error.value = err instanceof Error ? err.message : t('artifact.loadListError');
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
    emit('toast', err instanceof Error ? err.message : t('artifact.openError'));
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
        <h3 id="artifactBrowserTitle">{{ t('artifact.title') }}</h3>
        <p>{{ t('artifact.help') }}</p>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadList">
        {{ loading ? t('common.loading') : t('common.refresh') }}
      </button>
    </div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div v-if="artifacts.length" class="artifact-grid">
      <aside class="artifact-list" :aria-label="t('artifact.list')">
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
          <strong>{{ selected || t('artifact.fallback') }}</strong>
          <span v-if="payloadLoading">{{ t('common.loadingEllipsis') }}</span>
        </div>
        <pre v-if="prettyPayload" tabindex="0"><code>{{ prettyPayload }}</code></pre>
      </article>
    </div>
    <p v-else-if="!loading" class="empty-note">{{ t('artifact.empty') }}</p>
  </section>
</template>
