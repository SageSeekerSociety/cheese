<script setup lang="ts">
// 「改动」页的默认那一面：这次改到的每一个文件一段，段头是路径和增删数。
//
// 段头下面是那一段的差异，默认收着：一件活改几十个文件是常态，全都铺开等于把「改了
// 哪些东西」这一眼要看的清单埋在几千行差异里。点段头铺开一处，要看全文或微调就点段
// 头的「打开」，那一份单独开在这一格里。
//
// 「哪几段开着」不归这一只：打开一份文件时这一面整个被卸掉（`PanelChangesView` 的
// `v-else`），状态留在这里的话，读完一份返回时刚铺开的那几段又都收回去。所以由
// `PanelChangesView` 记着（`expanded`），这里只报「点了哪一段」出去。
//
// 它不自己滚：外面那一列（顶部还有这次交付的情况）一起滚，所以段头里的位置由外面问
// （`scrollTo`）。
import type { DiffReview, ReviewComment, ReviewCommentDraft } from '@/types/reviewComment'
import type { FileDiff } from '../../lib/diff'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { numberDiffLines, parseDiffLines } from '../../lib/diff'
import { placeLabel } from '../../lib/reviewPlace'
import ReviewCommentBox from '../review/ReviewCommentBox.vue'
import ReviewCommentCard from '../review/ReviewCommentCard.vue'

import ChangesDiff from './ChangesDiff.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { whenIdle } from '@/lib/idle'

const props = withDefaults(
  defineProps<{
    diffs: FileDiff[]
    review?: DiffReview | null
    /** 哪几段铺开着（路径）。默认一段都不铺：「改了哪些文件」是这一面第一眼要看的东西。 */
    expanded?: Set<string>
  }>(),
  { review: null, expanded: () => new Set<string>() }
)

// 每个文件的行只在 diff 换了时重算：模板里现算的话，每画一次都是一份新数组，下面那
// 一段会当它换了文件，把点开的「显示剩余」收回去。
const linesByPath = computed(() => new Map(props.diffs.map((d) => [d.path, parseDiffLines(d.body)])))

const emit = defineEmits<{
  (e: 'open', path: string): void
  (e: 'comment', draft: ReviewCommentDraft): void
  (e: 'edit-comment', id: string, body: string, suggestion: string | null): void
  (e: 'remove-comment', id: string): void
  /** 点了段头：这一段铺开或收起。状态在 `PanelChangesView`（见文件头）。 */
  (e: 'toggle-section', path: string): void
}>()

// ---- 批注：挂在它说的那几行下面。 ----
// 正在写的那一条：哪个文件、哪几行、那几行现在的字。
const composing = ref<{ path: string; start: number; end: number; text: string } | null>(null)

/** 这一条该挂在哪一行下面：未发送的按写的时候那几行的末行，送出过的按那几行在这一版里
 *  的位置；那几行已经不在了是 null。 */
function anchorOf(c: ReviewComment): number | null {
  if (c.state === 'draft') return c.line_end
  return c.current_line === null ? null : c.current_line + (c.line_end - c.line_start)
}
// 这一版的差异里露出来的那些行：挂不上去的批注放在文件头上。
const shownLines = computed(
  () =>
    new Map(
      props.diffs.map((d) => [
        d.path,
        new Set(
          numberDiffLines(linesByPath.value.get(d.path) ?? [])
            .filter((r) => r.kind !== 'del' && r.newNumber != null)
            .map((r) => r.newNumber as number)
        ),
      ])
    )
)
const topLevel = computed(() => (props.review?.comments ?? []).filter((c) => !c.parent_id))
function repliesOf(id: string) {
  return (props.review?.comments ?? []).filter((c) => c.parent_id === id)
}
function atLine(path: string, line: number) {
  if (!shownLines.value.get(path)?.has(line)) return []
  return topLevel.value.filter((c) => c.path === path && anchorOf(c) === line)
}
function atHead(path: string) {
  const shown = shownLines.value.get(path)
  return topLevel.value.filter((c) => {
    if (c.path !== path) return false
    const at = anchorOf(c)
    return at === null || !shown?.has(at)
  })
}
function where(c: ReviewComment) {
  return placeLabel(c)
}
function onPick(path: string, start: number, end: number, text: string) {
  composing.value = { path, start, end, text }
}
function submitNew(body: string, suggestion: string | null) {
  const c = composing.value
  if (!c) return
  emit('comment', { path: c.path, line_start: c.start, line_end: c.end, line_text: c.text, body, suggestion })
  composing.value = null
}
function reply(parentId: string, body: string) {
  const parent = topLevel.value.find((c) => c.id === parentId)
  if (!parent) return
  emit('comment', {
    path: parent.path,
    line_start: parent.line_start,
    line_end: parent.line_end,
    line_text: parent.line_text,
    body,
    suggestion: null,
    parent_id: parentId,
  })
}

const opened = (path: string) => props.expanded.has(path)

// ---- 分批挂段 ----
//
// 一次把几十个文件、几千行差异全铺进 DOM，主线程要连着忙好几秒：手机上这一段时间
// 里连返回键的事件都排不上（`content-visibility` 只省排版和绘制，节点还是全建）。
// 先挂够一屏的几段，其余的等浏览器空下来一批批补进来。
//
// 这不是虚拟化：每一段最终都会挂上，Ctrl+F、读屏、复制整段照旧。滚到哪、点到哪个
// 文件也都是挂上之后的事（`scrollTo` 会先把目标那一段补出来）。
//
// 批的大小按**行**算而不是按文件：一个三四行的文件和一份四百行的文件代价差两个
// 数量级，按文件切会让一批大得没边。
const FIRST_ROWS = 600
const BATCH_ROWS = 600
/** 一批最多几段：全是二进制文件（0 行差异）时按行数算不出头，得按段数兜住。 */
const MAX_FILES_PER_BATCH = 20

const rowCounts = computed(() => props.diffs.map((d) => (linesByPath.value.get(d.path) ?? []).length))

/** 从 `from` 起凑够 `budget` 行，至少一段、最多 `MAX_FILES_PER_BATCH` 段；返回挂到第几段为止。 */
function upTo(from: number, budget: number): number {
  let rows = 0
  let at = from
  while (at < props.diffs.length && rows < budget && at - from < MAX_FILES_PER_BATCH) {
    rows += rowCounts.value[at] ?? 0
    at += 1
  }
  // 一段都没凑够时（空 diff、二进制、纯改名）也要往前走一段，别让循环卡在原地。
  return Math.min(Math.max(at, from + 1), props.diffs.length)
}

const mounted = ref(0)
const mountedDiffs = computed(() => props.diffs.slice(0, mounted.value))

let cancelFill: (() => void) | null = null
let generation = 0

function stopFill() {
  generation += 1
  cancelFill?.()
  cancelFill = null
}

function fillRest() {
  const gen = generation
  if (mounted.value >= props.diffs.length) return
  cancelFill = whenIdle(() => {
    if (gen !== generation) return
    mounted.value = upTo(mounted.value, BATCH_ROWS)
    fillRest()
  })
}

function startBatching() {
  stopFill()
  mounted.value = upTo(0, FIRST_ROWS)
  fillRest()
}

// 按「有哪几段」认一次换没换：父组件每次重算都会给一份新数组，按数组身份看的话，
// 文件清单晚到一步就会把已经挂好的一半收回去重来。段的增删才是真的要重新分批。
watch(() => props.diffs.map((d) => d.path).join('\n'), startBatching, { immediate: true })
onBeforeUnmount(stopFill)

// 段落的 id 由路径来，路径里什么字都可能有：编号比转义省事。
const root = ref<HTMLElement | null>(null)
async function scrollTo(path: string) {
  const index = props.diffs.findIndex((d) => d.path === path)
  if (index < 0) return
  // 目标那一段还没挂上：先补到它（连同它前面的），再滚。
  if (index >= mounted.value) {
    mounted.value = index + 1
    await nextTick()
  }
  const el = root.value?.querySelector<HTMLElement>(`[data-path="${CSS.escape(path)}"]`)
  if (!el) return
  // 滚过去的两种来路都是「我要看这一份」：点了树上的文件，点了顶部清单里的文件。
  // 那一段还收着就先铺开，不然滚到跟前只有一行段头。
  if (!opened(path)) emit('toggle-section', path)
  el.scrollIntoView({ block: 'start' })
}

function dirOf(path: string): string {
  const cut = path.lastIndexOf('/')
  return cut < 0 ? '' : path.slice(0, cut + 1)
}
function nameOf(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1)
}

defineExpose({ scrollTo })
</script>

<template>
  <div ref="root" class="diff-list">
    <p v-if="diffs.length === 0" class="diff-list__empty">{{ t('work.room.changes.noChanges') }}</p>
    <section v-for="d in mountedDiffs" :key="d.path" class="diff-file" :data-path="d.path">
      <div class="diff-file__head">
        <button
          type="button"
          class="diff-file__toggle"
          :aria-expanded="opened(d.path)"
          :title="d.path"
          @click="emit('toggle-section', d.path)"
        >
          <v-icon size="16" class="c-faint">{{ opened(d.path) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
          <span class="diff-file__path"
            ><span class="diff-file__dir">{{ dirOf(d.path) }}</span
            >{{ nameOf(d.path) }}</span
          >
          <span v-if="d.status === 'added'" class="diff-file__status">{{ t('work.room.changes.fileAdded') }}</span>
          <span v-else-if="d.status === 'removed'" class="diff-file__status">{{
            t('work.room.changes.fileRemoved')
          }}</span>
          <span class="diff-file__counts">
            <span class="diff-file__add">+{{ d.added }}</span>
            <span class="diff-file__del">−{{ d.removed }}</span>
          </span>
        </button>
        <BaseButton v-if="d.status !== 'removed'" kind="ghost" size="sm" @click="emit('open', d.path)">
          {{ t('work.room.changes.openFile') }}
        </BaseButton>
      </div>
      <template v-if="opened(d.path)">
        <ReviewCommentCard
          v-for="c in atHead(d.path)"
          :key="c.id"
          :comment="c"
          :replies="repliesOf(c.id)"
          :where="where(c)"
          :mine="c.author === review?.me"
          :writable="!!review?.writable"
          :agent-name="review?.agentName ?? ''"
          :busy="review?.busy"
          @edit="(id, b, s) => emit('edit-comment', id, b, s)"
          @remove="(id) => emit('remove-comment', id)"
          @reply="reply"
        />
        <ChangesDiff
          :lines="linesByPath.get(d.path) ?? []"
          inline
          :commentable="!!review?.writable"
          :selected="composing?.path === d.path ? composing : null"
          @pick="(s, e, text) => onPick(d.path, s, e, text)"
        >
          <template #after="{ line }">
            <ReviewCommentCard
              v-for="c in atLine(d.path, line)"
              :key="c.id"
              :comment="c"
              :replies="repliesOf(c.id)"
              :where="where(c)"
              :mine="c.author === review?.me"
              :writable="!!review?.writable"
              :agent-name="review?.agentName ?? ''"
              :busy="review?.busy"
              @edit="(id, b, s) => emit('edit-comment', id, b, s)"
              @remove="(id) => emit('remove-comment', id)"
              @reply="reply"
            />
            <ReviewCommentBox
              v-if="composing && composing.path === d.path && composing.end === line"
              :line-text="composing.text"
              :submit-label="t('work.room.review.add')"
              :busy="review?.busy"
              @submit="submitNew"
              @cancel="composing = null"
            />
          </template>
        </ChangesDiff>
      </template>
    </section>
  </div>
</template>

<style scoped>
.diff-list__empty {
  margin: 0;
  padding: 16px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.diff-file {
  border-bottom: 1px solid var(--line);
  /* 长清单里屏幕外的段落不排版：几十个文件一起铺开时，滚动不该等它们。 */
  content-visibility: auto;
  contain-intrinsic-size: auto 400px;
}
/* 段头贴在这一列顶上：读到一个文件中间，仍然看得见这是哪个文件。 */
.diff-file__head {
  position: sticky;
  top: var(--diff-sticky-top, 0);
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 4px;
  min-height: 36px;
  padding: 2px 8px 2px 4px;
  border-bottom: 1px solid var(--line);
  background: var(--fill);
}
.diff-file__toggle {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 4px;
  border-radius: var(--radius-sm);
  text-align: left;
  cursor: pointer;
}
.diff-file__toggle:hover {
  background: var(--fill-2);
}
.diff-file__path {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-weight: 600;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.diff-file__dir {
  color: var(--muted);
}
.diff-file__status {
  flex: none;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.diff-file__counts {
  display: inline-flex;
  flex: none;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.diff-file__add {
  color: var(--ok-ink);
}
.diff-file__del {
  color: var(--danger-ink);
}
</style>
