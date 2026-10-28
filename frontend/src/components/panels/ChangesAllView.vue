<script setup lang="ts">
// 改动面板没打开文件时的那一面：这次交付的情况在最上面（宿主塞进来的 `head` 插槽），
// 下面每个改到的文件一段，连着往下排，一起滚。每段的差异默认收着，点段头铺开。
//
// 从 `PanelChangesView` 里分出来的（那个文件到了行数上限）。滚出去以后那条细栏、文件
// 清单、以及「读到第几个文件」都在这里；外面只管把它画出来、以及在有人点树上的文件
// 时叫它滚过去（`reveal`）。
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'
import type { ReviewBundle } from '../../types/reviewComment'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { buildFileRows } from '../../lib/changesTree'

import ChangesDiffList from './ChangesDiffList.vue'
import ChangesMoreMenu from './ChangesMoreMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 树上列的文件：这一支改到的那些。 */
  treeFiles: WorkspaceFile[]
  diffByPath: Map<string, FileDiff>
  fileDiffs: FileDiff[]
  treeShown: boolean
  currentTask: RoomTask | undefined
  /** 这件任务的状态；结束了的任务还要说一句只读。 */
  sourceStatus: string
  fileSource: FileSource
  fileToolReady: boolean
  refreshing: boolean
  /**
   * 文件清单收不收着。「另 N 个文件」由这里说，但状态归外面：这一面在打开文件时被
   * 卸掉（`v-else`），状态留在这一面里的话，返回时展开的那份清单又收回去了。
   */
  summaryAll: boolean
  /** 这件任务的批注；宿主不给就不画（`composables/useReviewComments.ts`）。 */
  review?: ReviewBundle | null
  /** 哪几段铺开着（路径）。归 `PanelChangesView`，理由同 `summaryAll`。 */
  expanded: Set<string>
}>()

const emit = defineEmits<{
  'select-file': [path: string]
  /** 要挑一个不在改动里的文件：面板自己那只选择器归宿主管。 */
  'open-other': []
  'select-version': [source: FileSource]
  refresh: []
  'update:summaryAll': [value: boolean]
  /** 点了某一段的段头：铺开或收起它。 */
  'toggle-section': [path: string]
}>()

// 这一面按树上的顺序排（文件夹在前、按名字），和左边那一列从上往下对得上；
// git 吐出来的顺序和树不一样，读的人会以为漏了文件。
const listDiffs = computed(() =>
  buildFileRows({ files: props.treeFiles, diffByPath: props.diffByPath, collapsedDirs: new Set() })
    .map((row) => (row.type === 'file' ? props.diffByPath.get(row.path) : undefined))
    .filter((d): d is FileDiff => !!d)
)

// 文件树不在的时候，顶部那块下面列前几个文件，其余收在「另 N 个文件」里。
const SUMMARY_FIRST = 3
const summaryFiles = computed(() => (props.summaryAll ? listDiffs.value : listDiffs.value.slice(0, SUMMARY_FIRST)))
function dirOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut < 0 ? '' : path.slice(0, cut + 1)
}

// 顶部那块和文件清单滚出去以后，这一列顶上留一条细栏：现在读到第几个文件，以及
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

// 全部改动那一面说一共改了多少。
const totals = computed(() =>
  props.fileDiffs.reduce((acc, d) => ({ added: acc.added + d.added, removed: acc.removed + d.removed }), {
    added: 0,
    removed: 0,
  })
)

const diffList = ref<InstanceType<typeof ChangesDiffList> | null>(null)
/** 滚到某一份文件那一段。宿主在有人点树上的文件时叫它。 */
function reveal(path: string) {
  diffList.value?.scrollTo(path)
}
defineExpose({ reveal })

// 文件清单里点一个改过的文件：滚到它那一段。不在改动里的（理论上不会出现在这份清单
// 里）交给宿主去开。
async function pick(path: string) {
  if (!props.diffByPath.has(path)) {
    emit('select-file', path)
    return
  }
  await nextTick()
  reveal(path)
}
</script>

<template>
  <div class="changes-all">
    <div
      ref="scroller"
      class="changes-scroll"
      :style="{ '--diff-sticky-top': floatShown ? `${FLOAT_BAR_PX}px` : '0px' }"
      :class="{ 'changes-scroll--bar': !hasHead && props.fileToolReady }"
      @scroll.passive="onScroll"
    >
      <div ref="headBox"><slot name="head" /></div>
      <!-- Which files changed: a total row plus the first few files (with the
           tree column always on, that column is the tree and this keeps only the
           total). Clicking one jumps to its section. -->
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
          <!-- While the head block is up the thin bar has not appeared yet, so
               open-other and ⋯ sit on this row. -->
          <template v-if="hasHead">
            <v-spacer />
            <BaseButton
              v-if="props.fileToolReady"
              kind="ghost"
              size="sm"
              prepend-icon="mdi-magnify"
              @click="emit('open-other')"
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
        <template v-if="!props.treeShown && listDiffs.length">
          <button
            v-for="d in summaryFiles"
            :key="d.path"
            type="button"
            class="changes-summary__file"
            :title="d.path"
            @click="pick(d.path)"
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
            @click="emit('update:summaryAll', !props.summaryAll)"
          >
            {{
              props.summaryAll
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
        :expanded="props.expanded"
        @open="emit('select-file', $event)"
        @toggle-section="emit('toggle-section', $event)"
        @comment="(d) => void props.review?.add(d)"
        @edit-comment="(id, b, s) => void props.review?.edit(id, b, s)"
        @remove-comment="(id) => void props.review?.remove(id)"
      />
    </div>
    <!-- The thin bar after scrolling past the head: which file of how many. It
         does not name the file; that file's section header sticks right under
         this bar (ChangesDiffList's sticky row) and already carries the full
         path. -->
    <div v-if="floatShown && props.fileToolReady" class="changes-float">
      <span v-if="listDiffs.length" class="changes-float__count">{{
        t('work.room.changes.fileOfTotal', { current: currentIndex + 1, total: listDiffs.length })
      }}</span>
      <v-spacer />
      <BaseButton kind="ghost" size="sm" prepend-icon="mdi-magnify" @click="emit('open-other')">
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
</template>

<style scoped src="./changes-scroll.css"></style>

<style scoped>
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
.changes-float__count {
  flex: none;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  font-variant-numeric: tabular-nums;
}
</style>
