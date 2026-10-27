<script setup lang="ts">
// 版本对比态的正文：整篇照常排出来，新加的绿底，删掉的红底加删除线，改了几个字的那一段在
// 行内标出增删的字。
//
// 是整篇，不是改动清单（「修改记录」那一栏的 `DocVersionDiff` 才折起没改的段）：读者在
// 预览里要的是「在原文里看哪里变了」，前后文就是他认位置的依据。
//
// Markdown 没改的、整段加的、整段删的，都交给和阅读视图同一个渲染器排版（标题、列表、
// 表格照样是标题、列表、表格）；改了几个字的那一段排成文字，因为字级别的增删标记放不进
// 渲染器——标题和列表符号在那里单独认出来，看着仍是标题和列表。Word 的正文本来就只有
// 文字（`lib/docxText`），一段一行排。
//
// 每一处改动的第一块挂 `data-change="序号"`：提示条上的「上一处 / 下一处」靠它跳。
import type { Segment } from '@/lib/paragraphDiff'
import type { DiffRow } from '@/lib/versionDiff'

import { computed, ref } from 'vue'

import MarkdownView from '../../common/MarkdownView.vue'

import { t } from '@/i18n'
import { BLANK_LINE } from '@/lib/versionDiff'

const props = defineProps<{
  rows: readonly DiffRow[]
  /** 正文是 Markdown（按渲染器排）还是纯文字段落（Word）。 */
  markdown: boolean
  /** 这一格窄（手机、右栏）：纸边留白收小。 */
  compact?: boolean
}>()

type Block =
  | { kind: 'same' | 'add' | 'del'; lines: string[]; change: number | null }
  | { kind: 'changed'; segments: Segment[]; head: number; list: string; quote: boolean; change: number | null }

const HEADING = /^(#{1,6})\s+/
const LIST = /^\s*([-*+]|\d+[.)])\s+/
const QUOTE = /^>\s?/

/** 改了几个字的那一段：认出行首的标题、列表、引用符号，从开头那段没改的字里摘掉。 */
function changedBlock(segments: Segment[], change: number | null): Block {
  const clean = segments.map((seg) => ({ ...seg, text: seg.text.split(BLANK_LINE).join('') }))
  let head = 0
  let list = ''
  let quote = false
  const first = clean[0]
  if (props.markdown && first?.kind === 'same') {
    let text = first.text
    const h = HEADING.exec(text)
    const l = LIST.exec(text)
    const q = QUOTE.exec(text)
    if (h) {
      head = h[1].length
      text = text.slice(h[0].length)
    } else if (l) {
      list = /\d/.test(l[1]) ? l[1] : '•'
      text = text.slice(l[0].length)
    } else if (q) {
      quote = true
      text = text.slice(q[0].length)
    }
    clean[0] = { ...first, text }
  }
  return { kind: 'changed', segments: clean.filter((seg) => seg.text), head, list, quote, change }
}

const blocks = computed<Block[]>(() => {
  const out: Block[] = []
  let run = -1
  let inRun = false
  for (const row of props.rows) {
    if (row.kind === 'skip') continue
    const blank = 'text' in row && row.text === BLANK_LINE
    const changed = row.kind !== 'same'
    let change: number | null = null
    if (changed && !blank && !inRun) {
      run += 1
      change = run
    }
    inRun = changed && (inRun || !blank)
    if (row.kind === 'changed') {
      out.push(changedBlock(row.segments, change))
      continue
    }
    const line = blank ? '' : row.text
    const last = out[out.length - 1]
    // 同一种连着的几行拼成一块：Markdown 的列表、表格要整块交给渲染器才认得出来。
    if (last && last.kind === row.kind && change === null && props.markdown) last.lines.push(line)
    else out.push({ kind: row.kind, lines: [line], change })
  }
  return out
})

const root = ref<HTMLElement | null>(null)

/** 跳到第 `index` 处改动（0 起），让它停在视野中间并闪一下。 */
function focusChange(index: number) {
  const el = root.value?.querySelector<HTMLElement>(`[data-change="${index}"]`)
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.classList.remove('vd--flash')
  void el.offsetWidth
  el.classList.add('vd--flash')
}
defineExpose({ focusChange })
</script>

<template>
  <div ref="root" class="vd" data-testid="version-diff">
    <div class="vd__paper" :class="{ 'vd__paper--compact': compact }">
      <template v-for="(block, i) in blocks" :key="i">
        <div
          v-if="block.kind === 'changed'"
          class="vd__changed"
          :class="[block.head ? `vd__h vd__h${block.head}` : '', { vd__quote: block.quote }]"
          :data-change="block.change ?? undefined"
        >
          <span v-if="block.list" class="vd__bullet">{{ block.list }}</span>
          <template v-for="(seg, j) in block.segments" :key="j">
            <ins v-if="seg.kind === 'add'" class="vd__ins">{{ seg.text }}</ins>
            <del v-else-if="seg.kind === 'del'" class="vd__del">{{ seg.text }}</del>
            <span v-else>{{ seg.text }}</span>
          </template>
        </div>
        <div
          v-else
          class="vd__block"
          :class="`vd__block--${block.kind}`"
          :data-change="block.change ?? undefined"
          :aria-label="
            block.kind === 'add'
              ? t('work.room.doc.diffAdded')
              : block.kind === 'del'
                ? t('work.room.doc.diffRemoved')
                : undefined
          "
        >
          <MarkdownView v-if="markdown" :source="block.lines.join('\n')" class="vd__md" />
          <template v-else>
            <p v-for="(line, j) in block.lines" :key="j" class="vd__p">{{ line }}</p>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.vd {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 20px 16px 48px;
  background: var(--canvas);
}
/* 一张纸：和阅读视图里那一页同一种质感，读者知道这仍是那份文档，只是标出了改动。 */
.vd__paper {
  max-width: 680px;
  margin: 0 auto;
  padding: 40px 48px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-1);
  color: var(--text);
  font-size: 14.5px;
  line-height: 1.75;
}
.vd__paper--compact {
  padding: 20px 18px;
}
.vd__p,
.vd__changed {
  margin: 0 0 10px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.vd__block {
  border-radius: 2px;
}
.vd__md :deep(> :last-child) {
  margin-bottom: 0;
}
.vd__block + .vd__block,
.vd__block + .vd__changed,
.vd__changed + .vd__block {
  margin-top: 4px;
}
/* 整块新加：绿底，左边一道绿线，扫一眼就看得出这一块是新的。 */
.vd__block--add {
  background: color-mix(in srgb, var(--ok) 14%, var(--surface));
  box-shadow: inset 3px 0 0 var(--ok);
  padding: 2px 8px 2px 12px;
  color: var(--ok-ink);
}
/* 整块删掉：红底加删除线，仍留在原处——读者要看见它原来在哪。 */
.vd__block--del {
  background: color-mix(in srgb, var(--danger) 10%, var(--surface));
  box-shadow: inset 3px 0 0 var(--danger);
  padding: 2px 8px 2px 12px;
  color: var(--danger-ink);
  text-decoration: line-through;
}
.vd__block--add :deep(*),
.vd__block--del :deep(*) {
  color: inherit;
}
.vd__ins {
  background: color-mix(in srgb, var(--ok) 18%, var(--surface));
  color: var(--ok-ink);
  text-decoration: none;
  border-radius: 2px;
}
.vd__del {
  background: color-mix(in srgb, var(--danger) 12%, var(--surface));
  color: var(--danger-ink);
  text-decoration: line-through;
}
.vd__h {
  font-weight: 600;
  color: var(--ink);
}
.vd__h1 {
  font-size: 1.5em;
}
.vd__h2 {
  font-size: 1.3em;
}
.vd__h3 {
  font-size: 1.15em;
}
.vd__quote {
  padding-left: 12px;
  border-left: 3px solid var(--line-2);
  color: var(--muted);
}
.vd__bullet {
  display: inline-block;
  min-width: 1.4em;
  color: var(--muted);
}
/* 从提示条跳过来的那一处闪一下，眼睛找得到落在哪。 */
.vd--flash {
  animation: vd-flash 1.2s var(--ease-standard);
}
@keyframes vd-flash {
  0%,
  40% {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  100% {
    outline: 2px solid transparent;
    outline-offset: 2px;
  }
}
</style>
