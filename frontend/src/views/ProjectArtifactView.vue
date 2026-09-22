<script setup lang="ts">
// 清单上这一项自己的那一页 (#1085 结论二)：它现在是第几版，交出去的那一份长什么
// 样，以及交付过的每一版是什么。预览、下载、版本历史都在这一页上，不用绕去别的
// 地方——「点进去看看」如果只能看到一串交付记录，那看的还是记录，不是东西。
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
/** 上边那块预览画的是哪一版。默认最新的一版：来这一页多半是为了看当前这一版。 */
const selectedId = ref('')
let comparisonGeneration = 0
const beforeVersion = computed(() => artifact.value?.versions.find((v) => v.card_id === before.value))
const afterVersion = computed(() => artifact.value?.versions.find((v) => v.card_id === after.value))
const selectedVersion = computed(() => artifact.value?.versions.find((v) => v.card_id === selectedId.value) ?? null)

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
    selectedId.value = found.versions.at(-1)?.card_id ?? ''
  } catch (e) {
    if (props.artifactId !== artifactId || props.projectId !== projectId) return
    loadError.value = e instanceof Error ? e.message : t('tasks.artifactPage.loadError')
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
    actionError.value = e instanceof Error ? e.message : t('tasks.artifactVersion.downloadError')
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
    selectedId.value = ''
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

      <div
        v-if="loading && !artifact"
        class="py-8 text-center"
        role="status"
        :aria-label="t('tasks.artifactPage.loadingLabel')"
      >
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <template v-else-if="artifact">
        <header class="artifact-head">
          <div class="artifact-head__id">
            <h1 class="t-page-title">{{ artifact.name }}</h1>
            <p class="t-meta c-faint mt-1">
              <template v-if="artifact.version">
                {{ t('tasks.artifactVersion.number', { number: artifact.version }) }}
                <template v-if="artifact.delivered_at">
                  · {{ t('tasks.artifactPage.deliveredAt', { time: relTime(artifact.delivered_at) }) }}
                </template>
              </template>
              <template v-else>{{ t('tasks.artifactPage.notDelivered') }}</template>
            </p>
          </div>
          <v-btn
            v-if="current?.kind === 'file'"
            variant="flat"
            color="primary"
            :loading="downloading === current.card_id"
            @click="download(current)"
          >
            {{ t('tasks.artifactPage.downloadCurrent') }}
          </v-btn>
          <v-btn v-else-if="current?.kind === 'link' && current.url" variant="flat" color="primary" :href="current.url">
            {{ t('tasks.artifactPage.open') }}
          </v-btn>
        </header>

        <p v-if="actionError" role="alert" class="t-body c-danger mb-4">{{ actionError }}</p>

        <div v-if="newestFirst.length" class="artifact-body mt-6">
          <div class="artifact-main">
            <section>
              <h2 class="t-title mb-3">{{ t('tasks.artifactPage.preview') }}</h2>
              <ArtifactVersionPreview
                v-if="selectedVersion"
                :project-id="projectId"
                :artifact-id="artifactId"
                :version="selectedVersion"
              />
            </section>

            <section v-if="artifact.versions.length >= 2" class="comparison mt-8">
              <h2 class="t-title mb-4">{{ t('tasks.artifactComparison.title') }}</h2>
              <div class="comparison-selectors">
                <label class="t-body"
                  >{{ t('tasks.artifactComparison.before') }}
                  <select v-model="before" :aria-label="t('tasks.artifactComparison.before')">
                    <option v-for="version in newestFirst" :key="version.card_id" :value="version.card_id">
                      {{ t('tasks.artifactVersion.number', { number: version.number }) }} · {{ version.subject }}
                    </option>
                  </select>
                </label>
                <label class="t-body"
                  >{{ t('tasks.artifactComparison.after') }}
                  <select v-model="after" :aria-label="t('tasks.artifactComparison.after')">
                    <option v-for="version in newestFirst" :key="version.card_id" :value="version.card_id">
                      {{ t('tasks.artifactVersion.number', { number: version.number }) }} · {{ version.subject }}
                    </option>
                  </select>
                </label>
              </div>
              <p v-if="before === after" class="t-body c-muted mt-4">
                {{ t('tasks.artifactComparison.chooseTwo') }}
              </p>
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
                    showPreviews
                      ? t('tasks.artifactComparison.hidePreviews')
                      : t('tasks.artifactComparison.showPreviews')
                  }}</v-btn
                >
                <div v-if="showPreviews && beforeVersion && afterVersion" class="comparison-previews mt-4">
                  <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="beforeVersion" />
                  <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="afterVersion" />
                </div>
              </template>
            </section>
          </div>

          <!-- 版本列表在上边那块预览的旁边，不在它下面：点一行就换预览，两件事在
               同一个视野里，不用点完再滚回去看点了什么。 -->
          <aside class="artifact-rail">
            <h2 class="t-title mb-3">{{ t('tasks.artifactPage.history') }}</h2>
            <ul class="version-list">
              <li
                v-for="version in newestFirst"
                :key="version.card_id"
                class="version-row"
                :class="{ 'version-row--selected': version.card_id === selectedId }"
              >
                <button
                  type="button"
                  class="version-row__pick"
                  :aria-current="version.card_id === selectedId ? 'true' : undefined"
                  :aria-label="t('tasks.artifactPage.previewVersion', { number: version.number })"
                  @click="selectedId = version.card_id"
                >
                  <span class="version-row__no t-meta c-faint">{{
                    t('tasks.artifactVersion.number', { number: version.number })
                  }}</span>
                  <span class="version-row__id">
                    <span class="t-body version-row__subject">{{
                      version.subject || t('tasks.artifactPage.noSubject')
                    }}</span>
                    <span class="t-meta c-faint version-row__when">
                      <template v-if="when(version)">{{ when(version) }}</template>
                      <template v-if="version.decided_by">
                        · {{ t('tasks.artifactPage.acceptedBy', { name: version.decided_by }) }}
                      </template>
                    </span>
                  </span>
                </button>
                <v-btn
                  v-if="version.kind === 'file'"
                  size="small"
                  variant="text"
                  color="on-surface-variant"
                  :loading="downloading === version.card_id"
                  @click="download(version)"
                >
                  {{ t('tasks.artifactVersion.download') }}
                </v-btn>
                <v-btn
                  v-else-if="version.kind === 'link' && version.url"
                  size="small"
                  variant="text"
                  color="on-surface-variant"
                  :href="version.url"
                >
                  {{ t('tasks.artifactPage.open') }}
                </v-btn>
                <!-- 交出去的是一次合并，或者这一版早于交付物留存：两种都没有文件可
                     给，而它们不是同一件事，所以话也不一样。 -->
                <span v-else class="t-meta c-faint version-row__none">
                  {{
                    version.kind === 'merge'
                      ? t('tasks.artifactPage.mergeOnly')
                      : t('tasks.artifactPage.noRetainedFile')
                  }}
                </span>
              </li>
            </ul>
          </aside>
        </div>

        <div v-else class="py-8 text-center">
          <p class="t-body c-muted">{{ t('tasks.artifactPage.noVersions') }}</p>
          <p class="t-meta c-faint mt-1">{{ t('tasks.artifactPage.noVersionsHint') }}</p>
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
/* 预览在宽的一边，版本列表在窄的一边：一页文档铺满 1120px 才读得下去，而版本行
   只要放得下版号、说明和一颗按钮。 */
.artifact-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 280px);
  gap: 24px;
  align-items: start;
}
.artifact-main {
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
  gap: 8px;
  padding: 8px 8px 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}
/* 选中那一行换的是底色和描边，不换位置：换一版不该让整列跳一下。 */
.version-row--selected {
  border-color: var(--accent);
  background: var(--accent-wash);
}
.version-row__pick {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 4px 0;
  border: 0;
  background: none;
  color: inherit;
  text-align: left;
  cursor: pointer;
  border-radius: var(--radius-sm);
}
.version-row__no {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
.version-row__id {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.version-row__subject {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.version-row__none {
  flex: 0 0 auto;
  max-width: 96px;
  text-align: right;
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
@media (max-width: 960px) {
  .artifact-body {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 700px) {
  .comparison-selectors,
  .comparison-previews {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
