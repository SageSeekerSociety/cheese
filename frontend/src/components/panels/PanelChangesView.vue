<script setup lang="ts">
// 改动 tab: 这件任务干出来的东西，一个面看完。
//
// 它以前是两个半成品并排放着，中间一个分段开关：Git 那半是一坨没有语法着色、不能
// 按文件跳的裸 diff，文件那半是一棵不知道哪些文件被改过的树。想验收的人得先在
// 「改动」里读整块 diff 找出改了哪些文件，再切到「文件」里一个个翻出来看——两边
// 都不是一个能验收的面。
//
// 合成之后只有一棵树，标着每个文件改了多少。默认那一面把这次改到的文件连着往下排
// （顶部是宿主塞进来的这次交付的情况，`head` 插槽），每段的差异默认收着，点段头铺开
// 一段；点树上的文件就滚到它那一段并把它铺开。要看全文或微调就点段头的「打开」，那一
// 份单独开在这一格里（保存冲突的两条出路原样保留）。不在改动里的文件从「打开其他
// 文件」挑。
//
// **只认 props**：改过的文件、diff、当前打开哪一份、它读回来的正文，都是从外面
// 递进来的；点一份文件、按保存、切版本发事件出去。取数在
// `composables/usePanelChanges.ts`，`PanelChanges.vue` 那只薄容器把它接上——
// 于是这一格在测试和 /demo 里都只需要一串 props。
//
// 三个面各拆成了一个子组件（这个文件原先到了行数上限）：顶部那条横条
// `ChangesFileBar`、打开着的那一份 `ChangesFileContent`、没打开文件时的全部改动
// `ChangesAllView`。留在这里的是三面共用的东西：面板外形、文件树那一列（含拖动）、
// 取不到改动时那几格，以及三面之间要传的话。props、emits、画出来的 DOM 都照旧。
import type { LiveSourceFallback } from '../../composables/panelChanges/types'
import type { DocumentRevisionsBundle } from '../../composables/useDocumentRevisions'
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { DiffLine, FileDiff } from '../../lib/diff'
import type { FileKind } from '../../lib/fileKind'
import type { MergeConflict, OfficeComparison, ReviewBundle } from '../../types/reviewComment'

import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { buildFileRows } from '../../lib/changesTree'
import ReviewMergePicker from '../review/ReviewMergePicker.vue'

import ChangesAllView from './ChangesAllView.vue'
import ChangesFileBar from './ChangesFileBar.vue'
import ChangesFileContent from './ChangesFileContent.vue'
import ChangesFileTree from './ChangesFileTree.vue'
import ChangesOpenFile from './ChangesOpenFile.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { storageGet, storageSet } from '@/lib/browserStorage'

const props = defineProps<{
  topicId: string | null
  readOnly: boolean
  active?: boolean
  taskLoadError: string | null
  selectedTask: string | null
  currentTask: RoomTask | undefined
  /** 这件任务的状态；结束了的任务还要说一句只读。 */
  sourceStatus: string
  sourceUnavailable: boolean
  fileSource: FileSource
  fileToolReady: boolean
  loading: boolean
  refreshing: boolean
  errorMsg: string | null
  /** 现场工作区没及时响应时退到了哪一面；正常是 `none`。 */
  liveFallback?: LiveSourceFallback
  noRepo: boolean
  fileDiffs: FileDiff[]
  diffByPath: Map<string, FileDiff>
  /** 树上列的文件：这一支改到的那些。 */
  treeFiles: WorkspaceFile[]
  /** 「打开其他文件」从这里挑：这个来源里的全部文件。 */
  allFiles: WorkspaceFile[]
  openPath: string | null
  fileDraft: string
  fileSaving: boolean
  fileDirty: boolean
  fileVersion: string | null
  fileBinary: boolean
  fileTooLarge: boolean
  fileBytes: number
  fileReadOnly: boolean
  fileConflict: boolean
  /** 存的时候和别人的修改重叠了：逐处选一个版本。 */
  fileMerge?: MergeConflict | null
  agentName?: string
  openDiff: FileDiff | null
  openDiffLines: DiffLine[]
  effectiveView: 'diff' | 'edit'
  fileView: 'diff' | 'edit'
  openIsImage: boolean
  openIsDocument: boolean
  openDocumentType: FileKind | null
  /** 修订只长在 .docx 上，别的时候这里给的是 null。 */
  revisionPath: string | null
  openImageSrc: string
  /** 图片取不到时的原因；空串就是没出错。 */
  openImageError: string
  collapsedDirs: Set<string>
  revealTick: number
  draftCount: number
  docBytes: ArrayBuffer | null
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
  /** 这一份 .docx 的修订：清单、只读、处理动作都在里面（`useDocumentRevisions.ts`）。 */
  revs: DocumentRevisionsBundle
  /** 这件任务的批注；宿主不给就不画（`composables/useReviewComments.ts`）。 */
  review?: ReviewBundle | null
  /** 打开的 Office 文件和上一版的比较（`usePanelChanges` 的那几个）。 */
  canCompare?: boolean
  comparing?: boolean
  comparison?: OfficeComparison | null
  comparisonLoading?: boolean
  comparisonError?: string
}>()

const emit = defineEmits<{
  'select-file': [path: string]
  /** 回到全部改动那一面。 */
  'close-file': []
  'select-version': [source: FileSource]
  /** 现场工作区没答上来之后，再试一次现场来源。 */
  'retry-live': []
  'toggle-dir': [path: string]
  refresh: []
  download: []
  save: []
  overwrite: []
  'resolve-merge': [content: string]
  'cancel-merge': []
  'toggle-compare': []
  reload: []
  'view-changed': [view: 'diff' | 'edit']
  'draft-changed': [content: string]
}>()

const { mdAndUp } = useDisplay()

// 文件树只在这一格够宽时常驻（铺满、或者面板拉得很宽）：并排时一列文件树把差异挤到
// 只剩半屏，那时文件清单在顶部那块下面，点一个就跳到它那一段。量不到宽度的时候（测试
// 环境、第一帧）按宽的画。
const WIDE_PX = 880
const root = ref<HTMLElement | null>(null)
const width = ref(0)
let resizeObserver: ResizeObserver | null = null
onMounted(() => {
  if (!root.value || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver((entries) => {
    width.value = entries[0]?.contentRect.width ?? 0
  })
  resizeObserver.observe(root.value)
})
onBeforeUnmount(() => resizeObserver?.disconnect())
const treeShown = computed(() => mdAndUp.value && (width.value === 0 || width.value >= WIDE_PX))

// 文件树那一列的宽度：拖它和编辑区之间那条线来改，记在这个浏览器里。路径长的仓库
// 一列 150px 只剩「old…」「wee…」，认不出是哪个文件。
const TREE_WIDTH_KEY = 'cheesex.changesTreeWidth'
const TREE_WIDTH = { min: 140, max: 520, initial: 220 }
function clampTreeWidth(px: number): number {
  return Math.round(Math.min(TREE_WIDTH.max, Math.max(TREE_WIDTH.min, px)))
}
function readTreeWidth(): number {
  // 没存过、读不出来都是 null，Number 出来是 0，就是没记过。
  const stored = Number(storageGet(TREE_WIDTH_KEY))
  return stored ? clampTreeWidth(stored) : TREE_WIDTH.initial
}
const treeWidth = ref(readTreeWidth())
function setTreeWidth(px: number) {
  treeWidth.value = clampTreeWidth(px)
  // 存不下只是下次回到默认宽度。
  storageSet(TREE_WIDTH_KEY, String(treeWidth.value))
}
function startTreeDrag(e: MouseEvent) {
  const body = (e.currentTarget as HTMLElement).parentElement
  if (!body) return
  const left = body.getBoundingClientRect().left
  const move = (ev: MouseEvent) => setTreeWidth(ev.clientX - left)
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
// 树上点一个改过的文件：回到全部改动那一面，滚到它那一段。没打开文件的时候全部改动
// 那一面本来就在，`reveal` 直接生效。
const allView = ref<InstanceType<typeof ChangesAllView> | null>(null)
// 「另 N 个文件」展不展开，由这一面点、状态放在这里：打开一份文件时全部改动那一面被卸掉
// （`v-else`），状态跟着它走的话，返回时展开的那份清单又收回去了。
const summaryAll = ref(false)
// 哪几段的差异铺开着（路径），同样归这一面，理由同上：读完一份文件返回时，刚才铺开的
// 那几段还该是铺开的。默认一段都不铺（空集合）——先给一眼清单，要看哪一段点哪一段。
const expandedSections = ref(new Set<string>())
function toggleSection(path: string) {
  const next = new Set(expandedSections.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedSections.value = next
}
async function pickFile(path: string) {
  if (!props.diffByPath.has(path)) {
    emit('select-file', path)
    return
  }
  if (props.openPath) emit('close-file')
  await nextTick()
  allView.value?.reveal(path)
}

const pickerOpen = ref(false)
function openOther(path: string) {
  pickerOpen.value = false
  emit('select-file', path)
}

// 树上那些行：折成文件夹再摊平这件事在 lib/changesTree.ts（纯函数）。
const fileRows = computed(() =>
  buildFileRows({
    files: props.treeFiles,
    diffByPath: props.diffByPath,
    collapsedDirs: props.collapsedDirs,
  })
)
</script>

<template>
  <div ref="root" class="panel-changes">
    <ChangesFileBar
      :open-path="props.openPath"
      :source-status="props.sourceStatus"
      :file-dirty="props.fileDirty"
      :open-diff="props.openDiff"
      :open-is-document="props.openIsDocument"
      :effective-view="props.effectiveView"
      :file-read-only="props.fileReadOnly"
      :file-saving="props.fileSaving"
      :current-task="props.currentTask"
      :file-source="props.fileSource"
      :file-tool-ready="props.fileToolReady"
      :refreshing="props.refreshing"
      @close-file="emit('close-file')"
      @view-changed="emit('view-changed', $event)"
      @save="emit('save')"
      @select-version="emit('select-version', $event)"
      @download="emit('download')"
      @refresh="emit('refresh')"
    />
    <!-- 这一格取不到东西（还在取、取失败、没有可看的版本、没有仓库）时，宿主塞进来的
         那块交付情况照样在最上面：审阅和决定不该因为差异没取到就跟着不见。转圈，不是
         骨架：这块地方长出来的是一套工具，等的是什么形状这里并不知道。判据同
         PanelPreview。 -->
    <div
      v-if="props.taskLoadError || props.sourceUnavailable || props.noRepo || props.loading || props.errorMsg"
      class="changes-scroll"
    >
      <slot v-if="!props.openPath" name="head" />
      <v-alert v-if="props.taskLoadError" type="error" density="compact" class="ma-4">{{
        props.taskLoadError
      }}</v-alert>
      <v-alert v-else-if="props.sourceUnavailable" type="warning" density="compact" class="ma-4">{{
        t('work.room.changes.sourceUnavailable')
      }}</v-alert>
      <p v-else-if="props.noRepo" class="source-note">{{ t('work.room.changes.noRepo') }}</p>
      <div v-else-if="props.loading" class="d-flex justify-center py-8">
        <v-progress-circular indeterminate color="primary" size="28" />
      </div>
      <v-alert v-else type="error" density="compact" class="ma-4 file-load-error">
        {{ props.errorMsg }}
        <BaseButton
          v-if="props.fileSource === 'live'"
          kind="secondary"
          size="sm"
          @click="emit('select-version', 'committed')"
          >{{ t('work.room.changes.switchToCommitted') }}</BaseButton
        >
      </v-alert>
    </div>
    <div v-else class="file-tool">
      <!-- 现场工作区没在读取预算里答上来：差异和文件已经退到已提交版本。这不是
           错误，不拿它顶掉整格——把现在看的是什么说清楚，再留一条回现场的出路。 -->
      <div v-if="props.liveFallback && props.liveFallback !== 'none'" class="live-fallback">
        <v-icon size="15" class="me-1">mdi-clock-alert-outline</v-icon>
        <span class="live-fallback__text">
          {{
            props.liveFallback === 'committed'
              ? t('work.room.changes.liveTimeoutCommitted')
              : t('work.room.changes.liveTimeoutUnavailable')
          }}
        </span>
        <BaseButton kind="ghost" size="sm" @click="emit('retry-live')">{{
          t('work.room.changes.retryLive')
        }}</BaseButton>
      </div>
      <!-- 保存冲突: 芝士 wrote this file after it was read. Show it and let the
         human choose — a silent winner is how edits vanished. -->
      <ReviewMergePicker
        v-if="props.fileMerge"
        :regions="props.fileMerge.regions"
        :agent-name="props.agentName ?? ''"
        :busy="props.fileSaving"
        @resolve="emit('resolve-merge', $event)"
        @cancel="emit('cancel-merge')"
      />
      <div v-else-if="props.fileConflict" class="file-conflict">
        <v-icon size="15" class="me-1">mdi-alert-outline</v-icon>
        <span class="file-conflict__text"> {{ t('work.room.changes.conflict') }} </span>
        <BaseButton kind="ghost" size="sm" @click="emit('reload')">{{
          t('work.room.changes.reloadLatest')
        }}</BaseButton>
        <BaseButton kind="danger" size="sm" :loading="props.fileSaving" @click="emit('overwrite')">
          {{ t('work.room.changes.saveAnyway') }}
        </BaseButton>
      </div>
      <div class="file-body">
        <ChangesFileTree
          v-if="treeShown"
          :rows="fileRows"
          :collapsed-dirs="props.collapsedDirs"
          :active-path="props.openPath"
          :reveal-tick="props.revealTick"
          :width="treeWidth"
          :empty-label="t('work.room.changes.noChanges')"
          @select="pickFile"
          @toggle-dir="emit('toggle-dir', $event)"
        />
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag handle for the pointer; without it the file tree keeps its width -->
        <div
          v-if="treeShown"
          class="tree-resizer"
          :title="t('work.topic.resize')"
          @mousedown.prevent="startTreeDrag"
          @dblclick="setTreeWidth(TREE_WIDTH.initial)"
        />
        <div class="file-editor">
          <ChangesFileContent
            v-if="props.openPath"
            :open-path="props.openPath"
            :open-is-document="props.openIsDocument"
            :doc-loading="props.docLoading"
            :doc-bytes="props.docBytes"
            :doc-error="props.docError"
            :doc-renderer-missing="props.docRendererMissing"
            :open-document-type="props.openDocumentType"
            :revs="props.revs"
            :revision-path="props.revisionPath"
            :review="props.review"
            :can-compare="props.canCompare"
            :comparing="props.comparing"
            :comparison="props.comparison"
            :comparison-loading="props.comparisonLoading"
            :comparison-error="props.comparisonError"
            :effective-view="props.effectiveView"
            :open-diff-lines="props.openDiffLines"
            :open-is-image="props.openIsImage"
            :open-image-src="props.openImageSrc"
            :open-image-error="props.openImageError"
            :file-binary="props.fileBinary"
            :file-too-large="props.fileTooLarge"
            :file-bytes="props.fileBytes"
            :file-draft="props.fileDraft"
            :file-read-only="props.fileReadOnly"
            @download="emit('download')"
            @save="emit('save')"
            @draft-changed="emit('draft-changed', $event)"
            @toggle-compare="emit('toggle-compare')"
          />
          <!-- 没打开文件时是全部改动那一面：这次交付的情况在最上面（宿主塞进来），下面
               每个改到的文件一段，连着往下排，一起滚；每段的差异默认收着，点段头铺开。 -->
          <ChangesAllView
            v-else
            ref="allView"
            :tree-files="props.treeFiles"
            :diff-by-path="props.diffByPath"
            :file-diffs="props.fileDiffs"
            :tree-shown="treeShown"
            :current-task="props.currentTask"
            :source-status="props.sourceStatus"
            :file-source="props.fileSource"
            :file-tool-ready="props.fileToolReady"
            :refreshing="props.refreshing"
            :summary-all="summaryAll"
            :review="props.review"
            :expanded="expandedSections"
            @select-file="emit('select-file', $event)"
            @open-other="pickerOpen = true"
            @select-version="emit('select-version', $event)"
            @refresh="emit('refresh')"
            @update:summary-all="summaryAll = $event"
            @toggle-section="toggleSection"
          >
            <template #head><slot name="head" /></template>
          </ChangesAllView>
        </div>
      </div>
    </div>
    <p v-if="props.draftCount" class="source-note source-drafts">
      {{ t('work.room.changes.draftsKept') }}
    </p>
    <ChangesOpenFile v-model="pickerOpen" :files="props.allFiles" @pick="openOther" />
  </div>
</template>

<style scoped src="./changes-scroll.css"></style>

<style scoped>
.source-note {
  margin: 0;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.source-drafts {
  border-top: 1px solid var(--line);
}

.panel-changes {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.file-load-error {
  flex: 0 0 auto;
}

/* 树上的变更标记。增删各自用 wash 底 + ink 字：mark 色（--ok / --danger）当文字
   在浅色主题下读不到 4.5:1，而这两个数字是要被读的，不是被瞥见的。
   走 `:deep()` 是因为行上的标记由子组件（`ChangesFileTree`）画，父组件的 scoped
   选择器本来落不到它的内部元素上；这一条和它自己的那一份标记共用这一个定义。 */
:deep(.file-mark) {
  flex: 0 0 auto;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
:deep(.file-mark--add) {
  color: var(--ok-ink);
  background: var(--ok-wash);
}
:deep(.file-mark--del) {
  color: var(--danger-ink);
  background: var(--danger-wash);
}

/* 逐文件 diff 的样式在 ChangesDiff.vue：渲染那一列的逻辑和它的长相都搬进了那个
   子组件（父组件的 scoped 选择器本来也落不到它内部）。 */

/* 文件: a two-pane browser — list + Monaco editor. Light, to match the app.
   Fills the tab height so the editor scrolls internally. */
.file-tool {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.file-conflict {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 6px 8px;
  font-size: 13px;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.07);
  border-bottom: 1px solid rgba(var(--v-theme-error), 0.25);
  flex: 0 0 auto;
}
.file-conflict__text {
  flex: 1 1 200px;
  min-width: 0;
}
/* 现场没答上来时那条说明：和冲突那条不同，它不是错误（红底会把「可以先看这个」
   读成「坏了」），用中性的底和墨色。 */
.live-fallback {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 6px 8px;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
  border-bottom: 1px solid var(--line);
  flex: 0 0 auto;
}
.live-fallback__text {
  flex: 1 1 200px;
  min-width: 0;
}
.file-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
/* 文件树和编辑区之间那条线：看得见的 1px，能抓的左右各多 4px（和对话、面板之间那条
   同一种画法）。 */
.tree-resizer {
  position: relative;
  z-index: var(--z-raised);
  flex: 0 0 1px;
  margin-left: -1px;
  cursor: col-resize;
  background: transparent;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.tree-resizer::before {
  content: '';
  position: absolute;
  inset: 0 -4px;
}
.tree-resizer:hover {
  background: var(--faint);
}
.file-editor {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
}
</style>
