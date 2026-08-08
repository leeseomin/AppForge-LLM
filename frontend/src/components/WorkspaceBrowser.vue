<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ApiError, getWorkspaceFile, getWorkspaceTree } from '../api';
import { useI18n } from '../i18n';
import type { WorkspaceTreeEntry, WorkspaceFilePayload } from '../types';

const { t } = useI18n();

const props = defineProps<{
  jobId: string;
}>();

const emit = defineEmits<{
  toast: [message: string];
}>();

const loading = ref(false);
const error = ref('');
const entries = ref<WorkspaceTreeEntry[]>([]);
const selectedPath = ref('');
const file = ref<WorkspaceFilePayload | null>(null);
const fileLoading = ref(false);
const query = ref('');

const files = computed(() => entries.value.filter((entry) => entry.type === 'file'));
const visibleFiles = computed(() => {
  const q = query.value.trim().toLowerCase();
  const source = q ? files.value.filter((entry) => entry.path.toLowerCase().includes(q)) : files.value;
  return source.slice(0, 250);
});

async function loadTree() {
  loading.value = true;
  error.value = '';
  try {
    const payload = await getWorkspaceTree(props.jobId);
    entries.value = payload.entries;
    if (!selectedPath.value) {
      const first = payload.entries.find((entry) => entry.type === 'file' && isReadableCandidate(entry.path));
      if (first) await openFile(first.path);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('workspace.loadTreeError');
  } finally {
    loading.value = false;
  }
}

async function openFile(path: string) {
  if (!path || fileLoading.value) return;
  selectedPath.value = path;
  fileLoading.value = true;
  try {
    file.value = await getWorkspaceFile(props.jobId, path);
  } catch (err) {
    const message = err instanceof ApiError ? err.payload.message : err instanceof Error ? err.message : t('workspace.openFileError');
    emit('toast', message);
  } finally {
    fileLoading.value = false;
  }
}

function isReadableCandidate(path: string) {
  return /\.(ts|tsx|js|jsx|vue|py|md|json|css|html|yaml|yml|toml)$/i.test(path);
}

function formatBytes(size?: number | null) {
  if (!size) return '';
  if (size < 1024) return `${size} B`;
  return `${(size / 1024).toFixed(size > 10240 ? 0 : 1)} KB`;
}

onMounted(loadTree);
watch(() => props.jobId, () => {
  selectedPath.value = '';
  file.value = null;
  loadTree();
});
</script>

<template>
  <section class="workspace-browser" aria-labelledby="workspaceBrowserTitle">
    <div class="panel-heading compact">
      <div>
        <h3 id="workspaceBrowserTitle">{{ t('workspace.title') }}</h3>
        <p>{{ t('workspace.help') }}</p>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadTree">
        {{ loading ? t('common.loading') : t('common.refresh') }}
      </button>
    </div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div class="workspace-split">
      <aside class="file-list" :aria-label="t('workspace.fileList')">
        <input v-model="query" type="search" :placeholder="t('workspace.search')" />
        <button
          v-for="entry in visibleFiles"
          :key="entry.path"
          type="button"
          :class="{ selected: selectedPath === entry.path }"
          @click="openFile(entry.path)"
        >
          <span>{{ entry.path }}</span>
          <small>{{ formatBytes(entry.size) }}</small>
        </button>
        <p v-if="!visibleFiles.length" class="empty-note">{{ t('workspace.empty') }}</p>
      </aside>
      <article class="code-viewer">
        <div class="code-toolbar">
          <strong>{{ file?.path || selectedPath || t('workspace.select') }}</strong>
          <span v-if="fileLoading">{{ t('common.loadingEllipsis') }}</span>
          <span v-else-if="file?.size">{{ formatBytes(file.size) }}</span>
        </div>
        <pre v-if="file" tabindex="0"><code>{{ file.content }}</code></pre>
        <p v-else class="empty-note">{{ t('workspace.selectHelp') }}</p>
      </article>
    </div>
  </section>
</template>
