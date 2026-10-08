<script setup lang="ts">
// 「改动」页的默认那一面：这次改到的每一个文件，差异一个接一个往下排。
//
// 审阅是从头读到尾的事，一次只能看一个文件、读完一个再去树上点下一个，读的人就得
// 自己记着读到哪了。这里每个文件一段，段头是路径和增删数，可以单独收起；要编辑或看
// 全文就点段头的「打开」，那一份单独开在这一格里。
//
// 它不自己滚：外面那一列（顶部还有这次交付的情况）一起滚，所以段头里的位置由外面问
// （`scrollTo`）。
import type { DiffReview, ReviewComment, ReviewCommentDraft } from '@/types/reviewComment'
import type { FileDiff } from '../../lib/diff'

import { computed, ref } from 'vue'

import { numberDiffLines, parseDiffLines } from '../../lib/diff'
import ReviewCommentBox from '../review/ReviewCommentBox.vue'
import ReviewCommentCard from '../review/ReviewCommentCard.vue'

import ChangesDiff from './ChangesDiff.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(defineProps<{ diffs: FileDiff[]; review?: DiffReview | null }>(), { review: null })

// 每个文件的行只在 diff 换了时重算：模板里现算的话，每画一次都是一份新数组，下面那
// 一段会当它换了文件，把点开的「显示剩余」收回去。
const linesByPath = computed(() => new Map(props.diffs.map((d) => [d.path, parseDiffLines(d.body)])))

const emit = defineEmits<{
  (e: 'open', path: string): void
  (e: 'comment', draft: ReviewCommentDraft): void
  (e: 'edit-comment', id: string, body: string, suggestion: string | null): void
  (e: 'remove-comment', id: string): void
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
  if (c.state === 'sent' && c.current_line === null) return t('work.room.review.gone')
  const start = c.state === 'draft' ? c.line_start : (c.current_line as number)
  const end = start + (c.line_end - c.line_start)
  return t('work.room.review.lines', { lines: start === end ? `${start}` : `${start}–${end}` })
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

const collapsed = ref(new Set<string>())
function toggle(path: string) {
  const next = new Set(collapsed.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsed.value = next
}

// 段落的 id 由路径来，路径里什么字都可能有：编号比转义省事。
const root = ref<HTMLElement | null>(null)
function scrollTo(path: string) {
  const el = root.value?.querySelector<HTMLElement>(`[data-path="${CSS.escape(path)}"]`)
  if (!el) return
  if (collapsed.value.has(path)) toggle(path)
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
    <section v-for="d in diffs" :key="d.path" class="diff-file" :data-path="d.path">
      <div class="diff-file__head">
        <button
          type="button"
          class="diff-file__toggle"
          :aria-expanded="!collapsed.has(d.path)"
          :title="d.path"
          @click="toggle(d.path)"
        >
          <v-icon size="16" class="c-faint">{{
            collapsed.has(d.path) ? 'mdi-chevron-right' : 'mdi-chevron-down'
          }}</v-icon>
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
      <template v-if="!collapsed.has(d.path)">
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
