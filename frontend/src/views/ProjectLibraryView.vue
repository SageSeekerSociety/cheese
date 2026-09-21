<script setup lang="ts">
// 资料库：你给这个项目的文件。
//
// 上传仍然只走输入栏——一份资料总是在说某件事的时候给进来的，这一页不再开第二个
// 入口。它回答的是另外两个问题：给进去的都有什么，以及不要了的怎么扔掉。没有这
// 一页时这两件事都只能在 @ 菜单里猜。
//
// 同名不覆盖，所以列表里会出现 `预算表(2).xlsx`：两次上传就是两份，各自留着。
import type { LibraryFile } from '../api'

import { ref, watch } from 'vue'

import { deleteLibraryFile, downloadFile, libraryFileRawUrl, listProjectLibrary } from '../api'

const props = defineProps<{ projectId: string }>()

const files = ref<LibraryFile[]>([])
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const removing = ref('')
const confirming = ref<LibraryFile | null>(null)

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = ''
  try {
    const listed = await listProjectLibrary(projectId)
    if (props.projectId !== projectId) return
    files.value = listed.data
  } catch (e) {
    if (props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : '未能读取资料库'
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

async function download(file: LibraryFile) {
  actionError.value = ''
  try {
    await downloadFile(libraryFileRawUrl(props.projectId, file.path), file.path.split('/').pop() || file.path)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能下载这份资料'
  }
}

async function remove(file: LibraryFile) {
  confirming.value = null
  removing.value = file.path
  actionError.value = ''
  try {
    await deleteLibraryFile(props.projectId, file.path)
    files.value = files.value.filter((f) => f.path !== file.path)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能删除这份资料'
  } finally {
    removing.value = ''
  }
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function fmtWhen(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString()
}

watch(
  () => props.projectId,
  () => {
    files.value = []
    confirming.value = null
    actionError.value = ''
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="library-page pa-4 pa-md-6">
    <div class="library-content">
      <header class="d-flex align-center justify-space-between ga-4 mb-2">
        <h1 class="t-page-title">资料库</h1>
        <v-btn variant="text" color="on-surface-variant" :loading="loading" @click="load">刷新</v-btn>
      </header>
      <p class="t-body c-muted mb-6">你给这个项目的文件。每个对话都引用得到，芝士 只读不改</p>

      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>
      <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

      <div v-if="loading && !files.length" class="py-8 text-center" role="status" aria-label="读取资料库">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <ul v-else-if="files.length" class="library-list">
        <li v-for="file in files" :key="file.path" class="library-row">
          <v-icon icon="mdi-file-outline" size="20" class="library-row__icon" />
          <div class="library-row__id">
            <div class="library-row__name t-body">{{ file.path }}</div>
            <div class="t-meta c-faint">{{ fmtBytes(file.bytes) }} · {{ fmtWhen(file.modified) }}</div>
          </div>
          <v-btn size="small" variant="text" color="on-surface-variant" @click="download(file)">下载</v-btn>
          <v-btn
            size="small"
            variant="text"
            color="on-surface-variant"
            :loading="removing === file.path"
            @click="confirming = file"
          >
            删除
          </v-btn>
        </li>
      </ul>

      <div v-else-if="!loadError" class="library-empty py-8 text-center">
        <p class="t-body c-muted">暂无资料</p>
        <p class="t-meta c-faint mt-1">在对话里上传的文件会收进这里</p>
      </div>
    </div>

    <!-- 删除是不可逆的，而且这一份可能已经被好几条消息引用着：那些引用会随之
         打不开，所以这一下要问一句。 -->
    <v-dialog :model-value="!!confirming" max-width="420" @update:model-value="confirming = null">
      <v-card v-if="confirming">
        <v-card-title class="t-title">删除《{{ confirming.path }}》</v-card-title>
        <v-card-text class="t-body"> 删除后无法恢复，已经引用过它的消息也将打不开这份文件 </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" color="on-surface-variant" @click="confirming = null">取消</v-btn>
          <v-btn variant="text" color="error" @click="remove(confirming)">删除</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.library-content {
  max-width: 720px;
  margin: 0 auto;
}
.library-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.library-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.library-row__icon {
  color: var(--faint);
}
.library-row__id {
  flex: 1;
  min-width: 0;
}
.library-row__name {
  color: var(--text);
  word-break: break-all;
}
</style>
