<script setup lang="ts">
// 改动 tab: 这件任务干出来的东西，一个面看完。
//
// 它以前是两个半成品并排放着，中间一个分段开关：Git 那半是一坨没有语法着色、不能
// 按文件跳的裸 diff，文件那半是一棵不知道哪些文件被改过的树。想验收的人得先在
// 「改动」里读整块 diff 找出改了哪些文件，再切到「文件」里一个个翻出来看——两边
// 都不是一个能验收的面。
//
// 合成之后只有一棵树，标着每个文件改了多少。默认那一面把所有改动连着往下排（顶部是
// 宿主塞进来的这次交付的情况，`head` 插槽），点树上的文件就滚到它那一段；要看全文或
// 微调就点段头的「打开」，那一份单独开在这一格里（保存冲突的两条出路原样保留）。
// 不在改动里的文件从「打开其他文件」挑。
//
// **只认 props**：改过的文件、diff、当前打开哪一份、它读回来的正文，都是从外面
// 递进来的；点一份文件、按保存、切版本发事件出去。取数在
// `composables/usePanelChanges.ts`，`PanelChanges.vue` 那只薄容器把它接上——
// 于是这一格在测试和 /demo 里都只需要一串 props。
import type { DocumentRevisionsBundle } from '../../composables/useDocumentRevisions'
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { DiffLine, FileDiff } from '../../lib/diff'
import type { FileKind } from '../../lib/fileKind'
import type { MergeConflict, OfficeComparison, ReviewBundle } from '../../types/reviewComment'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { buildFileRows, fmtBytes } from '../../lib/changesTree'
import CodeEditor from '../CodeEditor.vue'
import ReviewDocument from '../review/ReviewDocument.vue'
import ReviewMergePicker from '../review/ReviewMergePicker.vue'

import ChangesDiff from './ChangesDiff.vue'
import ChangesDiffList from './ChangesDiffList.vue'
import ChangesFileTree from './ChangesFileTree.vue'
import ChangesMoreMenu from './ChangesMoreMenu.vue'
import ChangesOpenFile from './ChangesOpenFile.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

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
  noRepo: boolean
  missing: string | null
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
  openRawUrl: string
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
  (e: 'select-file', path: string): void
  /** 回到全部改动那一面。 */
  (e: 'close-file'): void
  (e: 'select-version', source: FileSource): void
  (e: 'toggle-dir', path: string): void
  (e: 'refresh'): void
  (e: 'download'): void
  (e: 'save'): void
  (e: 'overwrite'): void
  (e: 'resolve-merge', content: string): void
  (e: 'cancel-merge'): void
  (e: 'toggle-compare'): void
  (e: 'reload'): void
  (e: 'view-changed', view: 'diff' | 'edit'): void
  (e: 'draft-changed', content: string): void
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
  try {
    const stored = Number(localStorage.getItem(TREE_WIDTH_KEY))
    return stored ? clampTreeWidth(stored) : TREE_WIDTH.initial
  } catch {
    return TREE_WIDTH.initial
  }
}
const treeWidth = ref(readTreeWidth())
function setTreeWidth(px: number) {
  treeWidth.value = clampTreeWidth(px)
  try {
    localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth.value))
  } catch {
    // 存不下只是下次回到默认宽度。
  }
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
// 树上点一个改过的文件：回到全部改动那一面，滚到它那一段。
const diffList = ref<InstanceType<typeof ChangesDiffList> | null>(null)
async function pickFile(path: string) {
  if (!props.diffByPath.has(path)) {
    emit('select-file', path)
    return
  }
  if (props.openPath) emit('close-file')
  await nextTick()
  diffList.value?.scrollTo(path)
}

const pickerOpen = ref(false)
function openOther(path: string) {
  pickerOpen.value = false
  emit('select-file', path)
}

// 全部改动那一面按树上的顺序排（文件夹在前、按名字），和左边那一列从上往下对得上；
// git 吐出来的顺序和树不一样，读的人会以为漏了文件。
const listDiffs = computed(() =>
  buildFileRows({ files: props.treeFiles, diffByPath: props.diffByPath, collapsedDirs: new Set() })
    .map((row) => (row.type === 'file' ? props.diffByPath.get(row.path) : undefined))
    .filter((d): d is FileDiff => !!d)
)

// 文件树不在的时候，顶部那块下面列前几个文件，其余收在「另 N 个文件」里。
const SUMMARY_FIRST = 3
const summaryAll = ref(false)
const summaryFiles = computed(() => (summaryAll.value ? listDiffs.value : listDiffs.value.slice(0, SUMMARY_FIRST)))
function dirOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut < 0 ? '' : path.slice(0, cut + 1)
}

// 顶部那块和文件清单滚出去以后，这一列顶上留一条细栏：现在读到哪个文件、第几个，以及
// 「打开其他文件」和 ⋯。没有顶部那块（没有待审阅的卡）时它一直在。
const scroller = ref<HTMLElement | null>(null)
const summaryEl = ref<HTMLElement | null>(null)
const pastSummary = ref(false)
const currentIndex = ref(0)
const floatShown = computed(() => pastSummary.value || !hasHead.value)
// 顶部那块在不在：宿主没有待审阅的卡时它什么都不画，插槽在、内容是空的，所以量高度。
const headBox = ref<HTMLElement | null>(null)
const headHeight = ref(0)
const hasHead = computed(() => headHeight.value > 0)
let headObserver: ResizeObserver | null = null
watch(headBox, (el) => {
  headObserver?.disconnect()
  if (!el || typeof ResizeObserver === 'undefined') return
  headObserver = new ResizeObserver((entries) => {
    headHeight.value = entries[0]?.contentRect.height ?? 0
  })
  headObserver.observe(el)
})
onBeforeUnmount(() => headObserver?.disconnect())
const FLOAT_BAR_PX = 36
function onScroll() {
  const box = scroller.value
  const summary = summaryEl.value
  if (!box || !summary) return
  const top = box.scrollTop
  pastSummary.value = top >= summary.offsetTop + summary.offsetHeight
  const sections = Array.from(box.querySelectorAll<HTMLElement>('.diff-file'))
  let at = 0
  sections.forEach((el, i) => {
    if (el.offsetTop <= top + FLOAT_BAR_PX + 1) at = i
  })
  currentIndex.value = at
}
const currentDiff = computed(() => listDiffs.value[currentIndex.value] ?? null)

// 全部改动那一面说一共改了多少。
const totals = computed(() =>
  props.fileDiffs.reduce((acc, d) => ({ added: acc.added + d.added, removed: acc.removed + d.removed }), {
    added: 0,
    removed: 0,
  })
)

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
    <!-- 单独打开一份文件时的横条：返回、这份文件、它的两面（差异 / 全文），以及对它做的
         事。全部改动那一面没有这一条，顶部是这次交付的情况（宿主塞进来的 head）。 -->
    <div v-if="props.openPath" class="changes-bar" :class="{ 'changes-bar--phone': !mdAndUp }">
      <span v-if="mdAndUp && props.sourceStatus" class="source-status">{{ props.sourceStatus }}</span>
      <span v-if="mdAndUp && props.sourceStatus" class="changes-bar__sep" aria-hidden="true" />
      <BaseButton
        kind="ghost"
        icon="mdi-arrow-left"
        size="sm"
        :class="{ 'tap-target': !mdAndUp }"
        :title="t('work.room.changes.backToAll')"
        :aria-label="t('work.room.changes.backToAll')"
        @click="emit('close-file')"
      />
      <span class="changes-bar__path" :title="props.openPath">
        {{ mdAndUp ? props.openPath : props.openPath.split('/').pop() }}
      </span>
      <span v-if="props.fileDirty" class="changes-bar__dot" :title="t('work.room.changes.unsaved')" />
      <v-spacer v-if="mdAndUp" />
      <!-- 看 diff / 改文件是同一个文件的两面，只有改过的文件才有两面。文档没有
           这两面：它的差异是一句「二进制文件不同」，而按文本编辑会损坏它。 -->
      <div v-if="props.openDiff && !props.openIsDocument" class="seg seg--sm">
        <button
          type="button"
          class="seg__btn"
          :class="{ 'seg__btn--on': props.effectiveView === 'diff' }"
          @click="emit('view-changed', 'diff')"
        >
          {{ t('work.room.changes.diffView') }}
        </button>
        <button
          type="button"
          class="seg__btn"
          :class="{ 'seg__btn--on': props.effectiveView === 'edit' }"
          @click="emit('view-changed', 'edit')"
        >
          {{ props.fileReadOnly || !mdAndUp ? t('work.room.changes.fullText') : t('work.room.changes.edit') }}
        </button>
      </div>
      <!-- Read-only files (binary / oversized / images) get no 保存 button at
         all: saving one is what corrupted them. 手机上文件只读（见 CodeEditor
         那一处），也就没有保存；每份都是只读，不必每份再说一次。 -->
      <span v-if="mdAndUp && props.fileReadOnly" class="changes-bar__ro">{{ t('work.room.changes.readOnly') }}</span>
      <BaseButton
        v-else-if="mdAndUp && props.effectiveView === 'edit'"
        kind="primary"
        size="sm"
        :loading="props.fileSaving"
        :disabled="!props.fileDirty"
        @click="emit('save')"
      >
        {{ t('work.room.changes.save') }}
      </BaseButton>
      <ChangesMoreMenu
        :current-task="props.currentTask"
        :file-source="props.fileSource"
        :can-download="props.fileToolReady"
        :refreshing="props.refreshing"
        @select-version="emit('select-version', $event)"
        @download="emit('download')"
        @refresh="emit('refresh')"
      />
    </div>
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
      <!-- chip 指来的文件不在这件任务里。列表照常显示：读者可以在树上挑别的文件。 -->
      <v-alert
        v-if="props.missing"
        type="info"
        variant="tonal"
        density="compact"
        class="ma-2"
        data-testid="missing-file"
      >
        {{ t('work.room.changes.missingInTask', { path: props.missing }) }}
      </v-alert>
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
        <div
          v-if="treeShown"
          class="tree-resizer"
          :title="t('work.topic.resize')"
          @mousedown.prevent="startTreeDrag"
          @dblclick="setTreeWidth(TREE_WIDTH.initial)"
        />
        <div class="file-editor">
          <!-- 文档：画出这一版，再把它自己带的修订列在旁边。排在差异前面，因为
             一份 .docx 的差异只有一句「二进制文件不同」。 -->
          <div v-if="props.openPath && props.openIsDocument" class="doc-view">
            <div v-if="props.docLoading && !props.docBytes" class="file-blob">
              <v-progress-circular indeterminate color="primary" size="24" />
            </div>
            <div v-else-if="props.docRendererMissing && !props.docBytes" class="file-blob">
              <v-icon size="30" class="c-faint mb-2">mdi-eye-off-outline</v-icon>
              <div class="file-blob__title">{{ t('work.room.changes.docPreviewDisabled') }}</div>
              <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
                <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                {{ t('work.room.changes.downloadOriginal') }}
              </BaseButton>
            </div>
            <div v-else-if="props.docError && !props.docBytes" class="file-blob">
              <v-icon size="30" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
              <div class="file-blob__title">{{ t('work.room.changes.cantDisplay') }}</div>
              <div class="file-blob__note">{{ props.docError }}</div>
              <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
                <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                {{ t('work.room.changes.downloadOriginal') }}
              </BaseButton>
            </div>
            <ReviewDocument
              v-else
              class="doc-view__body"
              :path="props.openPath"
              :document-type="props.openDocumentType"
              :doc-bytes="props.docBytes"
              :revs="props.revs"
              :revision-path="props.revisionPath"
              :review="props.review"
              :can-compare="props.canCompare"
              :comparing="props.comparing"
              :comparison="props.comparison"
              :comparison-loading="props.comparisonLoading"
              :comparison-error="props.comparisonError"
              @toggle-compare="emit('toggle-compare')"
              @comment="(d) => void props.review?.add(d)"
              @edit-comment="(id, b, s) => void props.review?.edit(id, b, s)"
              @remove-comment="(id) => void props.review?.remove(id)"
            />
          </div>
          <!-- 逐文件 diff: 一个文件一段，增删各自着色。整块裸 diff 读不动，也没法
             定位到文件，所以验收动线以前根本立不起来。行号、折行、窗口化都在
             ChangesDiff 里。 -->
          <ChangesDiff v-else-if="props.openPath && props.effectiveView === 'diff'" :lines="props.openDiffLines" />
          <div v-else-if="props.openPath && props.openIsImage" class="file-image-view">
            <img :src="props.openRawUrl" :alt="props.openPath" />
          </div>
          <!-- Binary / oversized: no editor. Opening one in Monaco meant every
             byte utf-8 could not decode came back as U+FFFD, and 保存 wrote
             the damage to disk. -->
          <div v-else-if="props.openPath && (props.fileBinary || props.fileTooLarge)" class="file-blob">
            <v-icon size="30" class="c-faint mb-2">
              {{ props.fileTooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}
            </v-icon>
            <div class="file-blob__title">
              {{ props.fileTooLarge ? t('work.room.changes.tooLarge') : t('work.room.changes.binary') }}
            </div>
            <div class="file-blob__note">{{ props.openPath }} · {{ fmtBytes(props.fileBytes) }}</div>
            <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
              <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
              {{ t('work.room.changes.downloadOriginal') }}
            </BaseButton>
          </div>
          <!-- 手机上只读：软键盘配 Monaco 不是能救的组合，给一个明确的说法比给一个
             难用的编辑器好。 -->
          <CodeEditor
            v-else-if="props.openPath"
            :model-value="props.fileDraft"
            :filename="props.openPath"
            :readonly="!mdAndUp || props.fileReadOnly"
            @update:model-value="emit('draft-changed', $event)"
            @save="emit('save')"
          />
          <!-- 没打开文件时是全部改动那一面：这次交付的情况在最上面（宿主塞进来），下面
               每个改到的文件一段，连着往下排，一起滚。 -->
          <div v-else class="changes-all">
            <div
              ref="scroller"
              class="changes-scroll"
              :style="{ '--diff-sticky-top': floatShown ? `${FLOAT_BAR_PX}px` : '0px' }"
              :class="{ 'changes-scroll--bar': !hasHead && props.fileToolReady }"
              @scroll.passive="onScroll"
            >
              <div ref="headBox"><slot name="head" /></div>
              <!-- 改了哪些文件：一行总数，加上前几个文件（文件树常驻时它就是那棵树，这里
                   只留总数那一行）。点一个就跳到它那一段。 -->
              <div v-show="listDiffs.length" ref="summaryEl" class="changes-summary">
                <div class="changes-summary__row">
                  <span class="changes-summary__total">
                    {{
                      t('work.room.changes.summary', {
                        count: props.fileDiffs.length,
                        added: totals.added,
                        removed: totals.removed,
                      })
                    }}
                  </span>
                  <span v-if="props.currentTask && props.currentTask.status !== 'open'" class="c-faint">
                    {{ props.sourceStatus }}
                  </span>
                  <!-- 顶部那块在的时候，细栏还没出来，打开其他文件和 ⋯ 先在这一行上。 -->
                  <template v-if="hasHead">
                    <v-spacer />
                    <BaseButton
                      v-if="props.fileToolReady"
                      kind="ghost"
                      size="sm"
                      prepend-icon="mdi-file-search-outline"
                      @click="pickerOpen = true"
                    >
                      {{ t('work.room.changes.openOther') }}
                    </BaseButton>
                    <ChangesMoreMenu
                      :current-task="props.currentTask"
                      :file-source="props.fileSource"
                      :can-download="false"
                      :refreshing="props.refreshing"
                      @select-version="emit('select-version', $event)"
                      @refresh="emit('refresh')"
                    />
                  </template>
                </div>
                <template v-if="!treeShown && listDiffs.length">
                  <button
                    v-for="d in summaryFiles"
                    :key="d.path"
                    type="button"
                    class="changes-summary__file"
                    :title="d.path"
                    @click="pickFile(d.path)"
                  >
                    <span class="changes-summary__path"
                      ><span class="c-faint">{{ dirOf(d.path) }}</span
                      ><b>{{ d.path.slice(dirOf(d.path).length) }}</b></span
                    >
                    <span class="changes-summary__add">+{{ d.added }}</span>
                    <span class="changes-summary__del">−{{ d.removed }}</span>
                  </button>
                  <button
                    v-if="listDiffs.length > SUMMARY_FIRST"
                    type="button"
                    class="changes-summary__more"
                    @click="summaryAll = !summaryAll"
                  >
                    {{
                      summaryAll
                        ? t('work.room.changes.fewerFiles')
                        : t('work.room.changes.moreFiles', { count: listDiffs.length - SUMMARY_FIRST })
                    }}
                  </button>
                </template>
              </div>
              <ChangesDiffList
                ref="diffList"
                :diffs="listDiffs"
                :review="props.review"
                @open="emit('select-file', $event)"
                @comment="(d) => void props.review?.add(d)"
                @edit-comment="(id, b, s) => void props.review?.edit(id, b, s)"
                @remove-comment="(id) => void props.review?.remove(id)"
              />
            </div>
            <!-- 滚过顶部之后的那条细栏：读到哪个文件、第几个。 -->
            <div v-if="floatShown && props.fileToolReady" class="changes-float">
              <span class="changes-float__path" :title="currentDiff?.path">{{
                currentDiff ? currentDiff.path.split('/').slice(-2).join('/') : ''
              }}</span>
              <span v-if="listDiffs.length" class="changes-float__count"
                >{{ currentIndex + 1 }} / {{ listDiffs.length }}</span
              >
              <v-spacer />
              <BaseButton kind="ghost" size="sm" prepend-icon="mdi-magnify" @click="pickerOpen = true">
                {{ t('work.room.changes.openOther') }}
              </BaseButton>
              <ChangesMoreMenu
                :current-task="props.currentTask"
                :file-source="props.fileSource"
                :can-download="false"
                :refreshing="props.refreshing"
                @select-version="emit('select-version', $event)"
                @refresh="emit('refresh')"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
    <p v-if="props.draftCount" class="source-note source-drafts">
      {{ t('work.room.changes.draftsKept') }}
    </p>
    <ChangesOpenFile v-model="pickerOpen" :files="props.allFiles" @pick="openOther" />
  </div>
</template>

<style scoped>
.source-status {
  flex: 0 0 auto;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
}
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
.changes-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
.changes-bar__sep {
  flex: 0 0 auto;
  width: 1px;
  height: 16px;
  background: var(--line);
}
.changes-bar__path {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.changes-bar__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--muted);
  flex: 0 0 auto;
}
.changes-bar__ro {
  font-size: 12px;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  flex: 0 0 auto;
}
/* ⋯ 里「改动」那一项后面的计数：改过的文件有几个。 */
/* 分段开关 —— 下一张卡合并 Git 与 文件 时整块删掉。
   选中态靠「浮起来的一面」（surface 底 + 1px 描边 + ink 字重）而不是靠两档灰的
   明暗差：--fill 和 --surface 的明暗次序在两个主题之间是反的（浅色 surface #fff
   亮于 fill #f4f5f7，深色 surface #1b1d20 反而暗于 fill #212429），只差 6 级，
   深色下几乎看不出来 —— #526 修的侧栏选中态就是栽在这一条上。 */
.seg {
  display: inline-flex;
  gap: 2px;
}
.seg__btn {
  padding: 2px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.seg__btn:hover {
  color: var(--ink);
}
.seg__btn--on {
  background: var(--surface);
  border-color: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 全部改动那一面的滚动层：顶部那块、文件清单、所有差异一起滚。 */
.changes-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}
.changes-all {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
/* 没有顶部那块时细栏一直在，差异从它下面开始。 */
.changes-scroll--bar {
  padding-top: 36px;
}
.changes-summary {
  display: flex;
  flex-direction: column;
  padding: 8px 12px 12px 16px;
  border-bottom: 1px solid var(--line);
}
.changes-summary__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
}
.changes-summary__total {
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}
.changes-summary__file {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 2px 0;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-align: left;
  cursor: pointer;
}
.changes-summary__file:hover .changes-summary__path {
  text-decoration: underline;
}
.changes-summary__path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.changes-summary__add,
.changes-summary__del {
  flex: none;
  min-width: 32px;
  text-align: right;
}
.changes-summary__add {
  color: var(--ok-ink);
}
.changes-summary__del {
  color: var(--danger-ink);
}
.changes-summary__more {
  align-self: flex-start;
  padding: 2px 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.changes-summary__more:hover {
  color: var(--ink);
}
/* 滚过顶部之后贴在这一列顶上的细栏。 */
.changes-float {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 8px 0 16px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.changes-float__path {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.changes-float__count {
  flex: none;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
  font-variant-numeric: tabular-nums;
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
.file-blob {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 13px;
  color: var(--text);
}
.file-blob__note {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
  word-break: break-all;
}
.file-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
/* 手机上 360px 宽也要放下：← 路径 差异|全文 ⋯。让位的只有路径，其余不缩。 */
.changes-bar--phone {
  gap: 4px;
}
.changes-bar--phone .changes-bar__path {
  flex: 1 1 0;
}
.changes-bar--phone .seg {
  flex: none;
}
.changes-bar--phone .seg__btn {
  padding: 2px 8px;
  white-space: nowrap;
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
/* 文档那一面：页面在左，修订柱在右，和预览那一格同一个排法。 */
.doc-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.doc-view__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
@media (max-width: 720px) {
  .doc-view__body {
    flex-direction: column;
  }
}
.file-image-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow: auto;
  background: conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0) 0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: var(--shadow-1);
  /* The container's checkerboard is what says "transparent"; the image itself
     sits on the panel surface so a PNG with alpha is not slammed onto a white
     slab in the dark theme (GitHub's image viewer does the same). */
  background: var(--surface);
}
</style>
