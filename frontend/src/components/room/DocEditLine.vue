<script setup lang="ts">
// 一条「改了文档」：队友调工具改了一份文档留下的那一行（规则在 lib/docEditLine）。
//
// 在它自己那一轮里，它像 Claude Code 里一次工具调用那样画：一行淡字，前面一枚文档
// 记号，不再写队友的名字——这一轮已经说了是谁的。不在哪一轮里的，是一行淡字，谁做的
// 写成名字。名字都是不带 @ 的浅色 chip（提到，lib/refChip），不是 @chip：这一行谁也
// 不通知。点名字去这个人那里（外层对话栏按 data-handle 委派），不打开文档。
//
// 点这一行打开文档本身；行尾的「查看改动」打开时把改过的几处标出来，只有一段差异、没有
// 逐处改动的老式改动，在原地展开那段差异。
import type { Block } from '../../cx_types'
import type { DocLine } from '../../lib/docEditLine'
import type { DocReviewRequest, OpenedDocument } from '../../lib/docReview'
import type { RefNames } from '../../lib/refChip'

import { computed, ref } from 'vue'

import { parseDiffLines } from '../../lib/diff'
import { docLineCount, docLineSentence, docLineSentenceWithRefs } from '../../lib/docEditLine'
import { noticeText } from '../../lib/noticeText'
import { renderPlain } from '../../lib/renderMessage'

import { t } from '@/i18n'

const props = defineProps<{
  block: Block
  line: DocLine
  /** 画在队友自己那一轮里：不写是谁，前面带一枚文档记号。 */
  inTurn: boolean
  /** handle 读成名字。 */
  names: RefNames
}>()

const emit = defineEmits<{
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
}>()

const sentence = computed(() => docLineSentence(props.line, props.inTurn))
// 名字在一颗按钮里，所以它们不再各占一个 Tab 位：键盘上这一行就是「打开文档」。
const sentenceHtml = computed(() =>
  renderPlain(docLineSentenceWithRefs(props.line, props.inTurn), props.names, { focusable: false })
)
const count = computed(() => docLineCount(props.line))

const turnId = computed(() => props.block.turn_id ?? undefined)
const opened = computed<OpenedDocument | undefined>(() =>
  props.line.library ? { id: props.line.library.id, title: props.line.library.title } : undefined
)

function open(e: MouseEvent) {
  // 点的是名字：去这个人那里（对话栏委派），不打开文档。
  if ((e.target as HTMLElement | null)?.closest('.name-ref')) return
  if (opened.value) emit('open-resource', 'doc', turnId.value, undefined, opened.value)
  else emit('open-resource', 'doc', turnId.value)
}

// 有逐处改动的：打开文档，标出那几处。房间自己那份文档的，「谁让改的」跟着一起去，
// 文档那边按这个人的名字说「应谁要求」。
function review() {
  const review = { requester: props.line.library ? '' : props.line.requester ?? '', edits: props.line.edits }
  if (opened.value) emit('open-resource', 'doc', turnId.value, review, opened.value)
  else emit('open-resource', 'doc', turnId.value, review)
}

// 只有一段差异的：在这一行下面展开。
const diff = computed(() =>
  props.line.edits.length || props.line.library || props.line.suggested ? '' : noticeText(props.block, 'detail')
)
const diffOpen = ref(false)
const reviewable = computed(() => props.line.edits.length > 0 || !!diff.value)

// 文档 diff 里的空行不画出来：一行只有空白时留一个空格子比留一条假的「改动」好。
function diffText(text: string): string {
  const body = text.slice(1)
  return /^(?:\s|&nbsp;)*$/.test(body) ? '' : body
}
</script>

<template>
  <div class="doc-line" :class="{ 'doc-line--turn': inTurn }" data-testid="doc-edit-line">
    <div class="doc-line__row">
      <button type="button" class="doc-line__open" :title="sentence" @click="open">
        <v-icon v-if="inTurn" class="doc-line__icon" size="16" aria-hidden="true">
          mdi-file-document-edit-outline
        </v-icon>
        <span class="doc-line__text" data-user-content v-html="sentenceHtml" />
      </button>
      <span v-if="count" class="doc-line__count">· {{ count }}</span>
      <button
        v-if="reviewable"
        type="button"
        class="doc-line__review"
        :aria-expanded="diff ? diffOpen : undefined"
        @click="diff ? (diffOpen = !diffOpen) : review()"
      >
        {{ t('work.room.notice.viewChanges') }}
      </button>
    </div>
    <div v-if="diff && diffOpen" class="doc-line__diff" :aria-label="t('work.room.notice.docDiff')">
      <template v-for="(row, index) in parseDiffLines(diff)" :key="index">
        <div
          v-if="(row.kind === 'add' || row.kind === 'del') && diffText(row.text)"
          class="doc-line__diff-row"
          :class="`doc-line__diff-row--${row.kind}`"
          :aria-label="row.kind === 'add' ? t('work.room.notice.added') : t('work.room.notice.removed')"
        >
          <span class="doc-line__mark" aria-hidden="true">{{ row.kind === 'add' ? '+' : '−' }}</span>
          <span>{{ diffText(row.text) }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 一行淡字：字号、颜色跟着外框（AgentNoticeFrame 的正文是 13px / --muted）。 */
.doc-line {
  min-width: 0;
}
.doc-line__row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  min-height: 20px;
}
/* 整句是一颗按钮，打开文档。标题长的时候在一行里截断，悬停看全句。 */
.doc-line__open {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: color var(--dur-quick) var(--ease-standard);
}
.doc-line__open:hover,
.doc-line__open:focus-visible {
  color: var(--text);
}
.doc-line__open:hover .doc-line__text {
  text-decoration: underline;
}
.doc-line__icon {
  flex: none;
  color: var(--faint);
}
.doc-line__text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-line__count {
  flex: none;
  white-space: nowrap;
}
.doc-line__review {
  flex: none;
  margin-left: 2px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
  text-decoration: underline;
  text-decoration-color: var(--line-2);
  text-underline-offset: 3px;
  transition:
    color var(--dur-quick) var(--ease-standard),
    text-decoration-color var(--dur-quick) var(--ease-standard);
}
.doc-line__review:hover,
.doc-line__review:focus-visible {
  color: var(--text);
  text-decoration-color: currentColor;
}
/* 在一轮里的那一行，差异块和字对齐，记号那一格让出来。 */
.doc-line--turn .doc-line__diff {
  margin-left: 22px;
}
.doc-line__diff {
  margin-top: 6px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  color: var(--text);
}
.doc-line__diff-row {
  display: flex;
  gap: 8px;
  padding: 4px 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-line__mark {
  flex: 0 0 1em;
}
.doc-line__diff-row--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.doc-line__diff-row--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
</style>
