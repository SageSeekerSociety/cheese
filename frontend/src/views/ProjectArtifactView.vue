<script setup lang="ts">
// 清单上这一项自己的那一页 (#1085 结论二)：它现在是第几版，交付过的每一版是什么。
//
// 一版是一次交付，所以这一页上没有「保存」「上传新版本」——版本由交付长出来，和
// 清单本身一样。能下载的是**当时交出去的那一份**，不是现在从源重建一次的结果：半
// 年后依赖变了，重建出来的可能和当时交出去的不是同一个东西。
//
// 三种交法在这一页上长得不一样，因为它们确实不是一回事：一份文件给下载，一个地址
// 给打开，一次合并什么都不给——代码项目交出去的是主干往前走一步。
import type { ArtifactVersion, ProjectArtifactDetail } from '../api'

import { computed, ref, watch } from 'vue'

import { artifactVersionFileUrl, downloadFile, getProjectArtifact } from '../api'
import { relTime } from '../lib/relTime'

const props = defineProps<{ projectId: string; artifactId: string }>()

const artifact = ref<ProjectArtifactDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const downloading = ref('')

/** 最新的一版在最前面：人来这一页多半是为了拿当前这一版。 */
const newestFirst = computed(() => [...(artifact.value?.versions ?? [])].reverse())

/** 当前版本里能直接拿走的那一个 —— 有它才在标题旁边放一颗按钮。 */
const current = computed(() => newestFirst.value[0] ?? null)

async function load() {
  const { projectId, artifactId } = props
  loading.value = true
  loadError.value = ''
  try {
    const found = await getProjectArtifact(projectId, artifactId)
    if (props.artifactId !== artifactId) return
    artifact.value = found
  } catch (e) {
    if (props.artifactId !== artifactId) return
    loadError.value = e instanceof Error ? e.message : '未能读取这一项产物'
  } finally {
    if (props.artifactId === artifactId) loading.value = false
  }
}

async function download(version: ArtifactVersion) {
  if (!version.filename) return
  downloading.value = version.card_id
  actionError.value = ''
  try {
    await downloadFile(artifactVersionFileUrl(props.projectId, props.artifactId, version.card_id), version.filename)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '未能下载这一版'
  } finally {
    downloading.value = ''
  }
}

function when(version: ArtifactVersion): string {
  return version.delivered_at ? relTime(version.delivered_at) : ''
}

watch(
  [() => props.projectId, () => props.artifactId],
  () => {
    artifact.value = null
    actionError.value = ''
    void load()
  },
  { immediate: true }
)
</script>

<template>
  <div class="artifact-page pa-4 pa-md-6">
    <div class="artifact-content">
      <p v-if="loadError" role="alert" class="t-body c-danger mb-4">{{ loadError }}</p>

      <div v-if="loading && !artifact" class="py-8 text-center" role="status" aria-label="读取这一项产物">
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else-if="artifact">
        <header class="artifact-head">
          <div class="artifact-head__id">
            <h1 class="t-page-title">{{ artifact.name }}</h1>
            <p class="t-meta c-faint mt-1">
              <template v-if="artifact.version">
                第 {{ artifact.version }} 版
                <template v-if="artifact.delivered_at"> · 交付于 {{ relTime(artifact.delivered_at) }}</template>
              </template>
              <template v-else>尚未交付</template>
            </p>
          </div>
          <v-btn
            v-if="current?.kind === 'file'"
            variant="flat"
            color="primary"
            :loading="downloading === current.card_id"
            @click="download(current)"
          >
            下载当前版本
          </v-btn>
          <v-btn v-else-if="current?.kind === 'link' && current.url" variant="flat" color="primary" :href="current.url">
            打开
          </v-btn>
        </header>

        <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

        <h2 class="t-title mt-8 mb-3">版本历史</h2>

        <ul v-if="newestFirst.length" class="version-list">
          <li v-for="version in newestFirst" :key="version.card_id" class="version-row">
            <span class="version-row__no t-meta c-faint">第 {{ version.number }} 版</span>
            <div class="version-row__id">
              <div class="t-body version-row__subject">{{ version.subject || '这次交付没有留下说明' }}</div>
              <div class="t-meta c-faint">
                <template v-if="when(version)">{{ when(version) }}</template>
                <template v-if="version.decided_by"> · {{ version.decided_by }} 验收</template>
              </div>
            </div>
            <v-btn
              v-if="version.kind === 'file'"
              size="small"
              variant="text"
              color="on-surface-variant"
              :loading="downloading === version.card_id"
              @click="download(version)"
            >
              下载
            </v-btn>
            <v-btn
              v-else-if="version.kind === 'link' && version.url"
              size="small"
              variant="text"
              color="on-surface-variant"
              :href="version.url"
            >
              打开
            </v-btn>
            <!-- 交出去的是一次合并，或者这一版早于交付物留存：两种都没有文件可
                 给，而它们不是同一件事，所以话也不一样。 -->
            <span v-else class="t-meta c-faint version-row__none">
              {{ version.kind === 'merge' ? '交出去的是这次合并' : '这一版没有留存文件' }}
            </span>
          </li>
        </ul>

        <div v-else class="py-8 text-center">
          <p class="t-body c-muted">暂无交付</p>
          <p class="t-meta c-faint mt-1">有一次交付被验收后，这里会出现第一版</p>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.artifact-content {
  max-width: 720px;
  margin: 0 auto;
}
.artifact-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.artifact-head__id {
  min-width: 0;
}
.version-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 版号在左边自成一列，所以一眼扫下来是 7、6、5……而不是混在说明里。 */
.version-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.version-row__no {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
.version-row__id {
  flex: 1 1 auto;
  min-width: 0;
}
.version-row__subject {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.version-row__none {
  flex: 0 0 auto;
}
</style>
