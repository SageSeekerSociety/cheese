<script setup lang="ts">
// 逐文件 diff 那一列：行号 gutter + 增减底色 + 长行折行 + 大文件的窗口化。
//
// 它单独成一个组件，一半是因为 PanelChangesView 已经贴着 1000 行的上限，一半是
// 因为这些决定都只关于「怎么把一份 diff 画出来」——行号从哪来、多长算太长、折行
// 还是横滚。它不取数，只吃外面递进来的一串已经分好类的行。
import type { DiffLine } from '../../lib/diff'

import { computed, ref, watch } from 'vue'

import { DIFF_WINDOW, numberDiffLines } from '../../lib/diff'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '../../lib/virtualList'
import VirtualList from '../common/VirtualList.vue'

import { t } from '@/i18n'

const props = defineProps<{ lines: DiffLine[] }>()

const expanded = ref(false)

// 这一格自己滚（`.diff-view` 上写着 overflow: auto），所以虚拟化时它就是那份滚动容
// 器。模板 ref 要等挂完才落地，见 VirtualList 文件头那段。
const viewport = ref<HTMLElement | null>(null)

// 行是按序号认的：这里的行只会整份换掉、或者从前面拉长（点开「显示剩余」），从不在
// 中间重排。`item-key` 收的是「给你一样东西」，具体是什么自己认（同话题栏那一根）。
function diffRowKey(_row: unknown, index: number): number {
  return index
}

// 一份 diff 最多铺 DIFF_WINDOW 行，超出的先收着给一个按钮。整份铺出来正是这一格
// 卡死的原因，而验收要看的是「改了什么」，不是「一滴不漏地读完几万行」。
const rows = computed(() => numberDiffLines(props.lines))
const visible = computed(() => (expanded.value ? rows.value : rows.value.slice(0, DIFF_WINDOW)))
const hiddenCount = computed(() => rows.value.length - visible.value.length)

// gutter 的宽度按整份文件里最长的那个号码算，不是按现在画出来的那一段：窗口化
// 只画前 DIFF_WINDOW 行，按可见部分算的话，点开「显示剩余」露出第 1000 行时整列
// 会当场变宽，行号栏跳一下。用 ch 而不是 px，等宽字号改了它还是对的。
const gutterCh = computed(() => {
  let digits = 1
  for (const r of rows.value) {
    digits = Math.max(digits, String(r.oldNumber ?? 0).length, String(r.newNumber ?? 0).length)
  }
  return digits + 1
})

// 换一份文件（lines 整个换掉）时收回到第一段：上一份点开了「显示剩余」，新打开的
// 一份不该跟着一路铺开。
watch(
  () => props.lines,
  () => {
    expanded.value = false
  }
)
</script>

<template>
  <div ref="viewport" class="diff-view" :style="{ '--diff-gutter': `${gutterCh}ch` }">
    <!-- Wrapped lines make row heights variable, so this goes through VirtualList's
         measuring (virtua `Virtualizer`) mode, not a fixed height. The switch is the
         row count: a normal-sized diff (the folded DIFF_WINDOW slice, a small file)
         stays a plain list, so Ctrl+F and the screen reader still reach every row;
         only past VIRTUAL_LIST_CONTENT_THRESHOLD (lib/virtualList.ts) — which is
         what "show more" on a long file lands on — does it virtualize. -->
    <VirtualList
      :items="visible"
      :item-key="diffRowKey"
      :scroll-parent="viewport"
      :threshold="VIRTUAL_LIST_CONTENT_THRESHOLD"
      :estimated-size="19"
    >
      <template #item="{ item }">
        <div class="diff-line" :class="`diff-line--${item.kind}`">
          <span class="diff-line__num" aria-hidden="true">{{ item.oldNumber ?? '' }}</span>
          <span class="diff-line__num" aria-hidden="true">{{ item.newNumber ?? '' }}</span>
          <span class="diff-line__text">{{ item.text }}</span>
        </div>
      </template>
    </VirtualList>
    <!-- A very long diff stops here and offers the rest; the numbers feed the
         window, not the review. -->
    <button v-if="hiddenCount" type="button" class="diff-more" @click="expanded = true">
      {{ t('work.room.changes.diffMore', { count: hiddenCount }) }}
    </button>
  </div>
</template>

<style scoped>
.diff-view {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 6px 0;
  background: var(--surface);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}
/* 行号两列定宽、正文占剩下的：长行折行时行号留在第一行、正文继续往下走，而底色是
   整行的，所以一条长行折成几行之后，增减底色仍然是连续的一条。 */
.diff-line {
  display: flex;
  align-items: flex-start;
  padding: 0 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text);
}
.diff-line__num {
  flex: 0 0 var(--diff-gutter);
  text-align: right;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  user-select: none;
}
.diff-line__text {
  flex: 1 1 auto;
  min-width: 0;
  padding-left: 12px;
}
.diff-line--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.diff-line--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
/* hunk 头（@@）是这一段的坐标，不是内容：底色比正文重一档、加粗，和增删行一眼分
   开。 */
.diff-line--hunk {
  margin-top: 4px;
  background: var(--fill);
  color: var(--muted);
  font-weight: 600;
}
.diff-line--meta {
  color: var(--faint);
}
.diff-more {
  display: block;
  width: 100%;
  padding: 6px 12px;
  border: 0;
  border-top: 1px solid var(--line);
  background: var(--fill);
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  text-align: center;
  cursor: pointer;
}
.diff-more:hover {
  color: var(--ink);
}
</style>
