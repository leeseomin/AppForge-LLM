<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ApiError, getWorkspaceFile, getWorkspaceTree } from '../api';
import type { WorkspaceTreeEntry, WorkspaceFilePayload } from '../types';

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
    error.value = err instanceof Error ? err.message : '파일 트리를 불러오지 못했습니다.';
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
    const message = err instanceof ApiError ? err.payload.message : err instanceof Error ? err.message : '파일을 열 수 없습니다.';
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
        <h3 id="workspaceBrowserTitle">작업공간 코드</h3>
        <p>생성된 파일을 ZIP 다운로드 전에 바로 확인합니다.</p>
      </div>
      <button class="secondary-button" type="button" :disabled="loading" @click="loadTree">
        {{ loading ? '로딩 중' : '새로고침' }}
      </button>
    </div>
    <p v-if="error" class="inline-error">{{ error }}</p>
    <div class="workspace-split">
      <aside class="file-list" aria-label="파일 목록">
        <input v-model="query" type="search" placeholder="파일 검색" />
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
        <p v-if="!visibleFiles.length" class="empty-note">표시할 파일이 없습니다.</p>
      </aside>
      <article class="code-viewer">
        <div class="code-toolbar">
          <strong>{{ file?.path || selectedPath || '파일을 선택하세요' }}</strong>
          <span v-if="fileLoading">불러오는 중…</span>
          <span v-else-if="file?.size">{{ formatBytes(file.size) }}</span>
        </div>
        <pre v-if="file" tabindex="0"><code>{{ file.content }}</code></pre>
        <p v-else class="empty-note">왼쪽 목록에서 파일을 선택하세요.</p>
      </article>
    </div>
  </section>
</template>
