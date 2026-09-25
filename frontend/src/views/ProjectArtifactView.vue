<script setup lang="ts">
// 清单上这一项自己的那一页 (#1085 结论二)：它现在是第几版，交付过的每一版是什么。
//
// 一版是一次交付，所以这一页上没有「保存」「上传新版本」——版本由交付长出来，和
// 清单本身一样。能下载的是**当时交出去的那一份**，不是现在从源重建一次的结果：半
// 年后依赖变了，重建出来的可能和当时交出去的不是同一个东西。
//
import type { ArtifactComparison, ArtifactVersion, ProjectArtifactDetail } from '../api'

import { computed, ref, watch } from 'vue'

import { artifactVersionFileUrl, compareArtifactVersions, downloadFile, getProjectArtifact } from '../api'
import ArtifactVersionPreview from '../components/ArtifactVersionPreview.vue'
import { t } from '../i18n'
import { parseDiffLines } from '../lib/diff'
import { relTime } from '../lib/relTime'

const props = defineProps<{ projectId: string; artifactId: string }>()

const artifact = ref<ProjectArtifactDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const downloading = ref('')
const before = ref('')
const after = ref('')
const comparison = ref<ArtifactComparison | null>(null)
const comparing = ref(false)
const comparisonError = ref('')
const showPreviews = ref(false)
let comparisonGeneration = 0
const beforeVersion = computed(() => artifact.value?.versions.find((v) => v.card_id === before.value))
const afterVersion = computed(() => artifact.value?.versions.find((v) => v.card_id === after.value))

function comparisonNote(note: string | null): string {
  const messages: Record<string, string> = {
    oversized: t('tasks.artifactComparison.oversized'),
    binary: t('tasks.artifactComparison.binary'),
    document: t('tasks.artifactComparison.document'),
    unsupported: t('tasks.artifactComparison.unsupported'),
    unavailable: t('tasks.artifactComparison.unavailable'),
    source: t('tasks.artifactComparison.source'),
    link: t('tasks.artifactComparison.link'),
  }
  return note ? messages[note] : ''
}

watch([before, after, () => props.projectId, () => props.artifactId], async () => {
  const generation = ++comparisonGeneration
  comparison.value = null
  comparisonError.value = ''
  showPreviews.value = false
  comparing.value = false
  if (!before.value || !after.value || before.value === after.value) return
  comparing.value = true
  try {
    const result = await compareArtifactVersions(props.projectId, props.artifactId, before.value, after.value)
    if (generation !== comparisonGeneration) return
    comparison.value = result
    showPreviews.value =
      result.kind === 'link' ||
      result.kind === 'unavailable' ||
      (result.kind === 'file' && result.files.some((file) => file.diff === null))
  } catch (e) {
    if (generation === comparisonGeneration)
      comparisonError.value = e instanceof Error ? e.message : t('tasks.artifactComparison.loadError')
  } finally {
    if (generation === comparisonGeneration) comparing.value = false
  }
})

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
    if (props.artifactId !== artifactId || props.projectId !== projectId) return
    artifact.value = found
    before.value = found.versions.at(-2)?.card_id ?? ''
    after.value = found.versions.at(-1)?.card_id ?? ''
  } catch (e) {
    if (props.artifactId !== artifactId || props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : '未能读取这一项产物'
  } finally {
    if (props.artifactId === artifactId && props.projectId === projectId) loading.value = false
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
    before.value = ''
    after.value = ''
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

        <section v-if="artifact.versions.length >= 2" class="comparison mt-8">
          <h2 class="t-title mb-4">{{ t('tasks.artifactComparison.title') }}</h2>
          <div class="comparison-selectors">
            <label class="t-body"
              >{{ t('tasks.artifactComparison.before') }}
              <select v-model="before" :aria-label="t('tasks.artifactComparison.before')">
                <option v-for="version in newestFirst" :key="version.card_id" :value="version.card_id">
                  {{ t('tasks.artifactComparison.version', { number: version.number }) }} · {{ version.subject }}
                </option>
              </select>
            </label>
            <label class="t-body"
              >{{ t('tasks.artifactComparison.after') }}
              <select v-model="after" :aria-label="t('tasks.artifactComparison.after')">
                <option v-for="version in newestFirst" :key="version.card_id" :value="version.card_id">
                  {{ t('tasks.artifactComparison.version', { number: version.number }) }} · {{ version.subject }}
                </option>
              </select>
            </label>
          </div>
          <p v-if="before === after" class="t-body c-muted mt-4">{{ t('tasks.artifactComparison.chooseTwo') }}</p>
          <p v-else-if="comparing" class="t-body c-muted mt-4" role="status">
            {{ t('tasks.artifactComparison.loading') }}
          </p>
          <p v-else-if="comparisonError" class="t-body c-danger mt-4" role="alert">{{ comparisonError }}</p>
          <template v-else-if="comparison">
            <p v-if="comparison.note" class="t-body c-muted mt-4">{{ comparisonNote(comparison.note) }}</p>
            <p v-if="comparison.identical !== null" class="t-body mt-4" role="status">
              {{
                comparison.kind === 'link'
                  ? comparison.identical
                    ? t('tasks.artifactComparison.sameLink')
                    : t('tasks.artifactComparison.changedLink')
                  : comparison.identical
                    ? t('tasks.artifactComparison.identical')
                    : t('tasks.artifactComparison.changed')
              }}
            </p>
            <article v-for="file in comparison.files" :key="file.path" class="comparison-file mt-4">
              <h3 class="t-body pa-3">{{ file.path }}</h3>
              <p v-if="file.before_mode !== file.after_mode" class="t-body pa-3">
                {{ t('tasks.artifactComparison.fileMode') }} {{ file.before_mode || '—' }} →
                {{ file.after_mode || '—' }}
              </p>
              <p v-if="file.note" class="t-body c-muted pa-3">{{ comparisonNote(file.note) }}</p>
              <pre
                v-if="file.diff"
                class="comparison-diff t-body"
              ><span v-for="(line, index) in parseDiffLines(file.diff)" :key="index" :class="`diff-${line.kind}`">{{ line.text }}</span></pre>
            </article>
            <v-btn
              v-if="comparison.kind === 'file'"
              class="mt-4"
              variant="text"
              @click="showPreviews = !showPreviews"
              >{{
                showPreviews ? t('tasks.artifactComparison.hidePreviews') : t('tasks.artifactComparison.showPreviews')
              }}</v-btn
            >
            <div v-if="showPreviews && beforeVersion && afterVersion" class="comparison-previews mt-4">
              <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="beforeVersion" />
              <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="afterVersion" />
            </div>
          </template>
        </section>

        <h2 class="t-title mt-8 mb-3">版本历史</h2>

        <ul v-if="newestFirst.length" class="version-list">
          <li v-for="version in newestFirst" :key="version.card_id" class="version-row">
            <span class="version-row__no t-meta c-faint">第 {{ version.number }} 版</span>
            <div class="version-row__id">
              <div class="t-body version-row__subject">{{ version.subject || '这次交付没有留下说明' }}</div>
              <div class="t-meta c-faint">
                <template v-if="when(version)">{{ when(version) }}</template>
                <template v-if="version.decided_by"> · {{ version.decided_by }} 采纳</template>
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
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.artifact-content {
  max-width: 1120px;
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
.comparison-selectors,
.comparison-previews {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.comparison-selectors label {
  min-width: 0;
}
.comparison-selectors select {
  display: block;
  width: 100%;
  margin-top: 8px;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
}
.comparison-file {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.comparison-file h3 {
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
}
.comparison-diff {
  overflow: auto;
  max-height: 480px;
}
.comparison-diff span {
  display: block;
  min-width: 100%;
  min-height: var(--lh-14);
  width: max-content;
  padding: 0 12px;
}
.diff-add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.diff-del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.diff-hunk,
.diff-meta {
  color: var(--muted);
  background: var(--fill);
}
@media (max-width: 700px) {
  .comparison-selectors,
  .comparison-previews {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
