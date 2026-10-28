<script setup lang="ts">
// 代码查看器：一份源码按行摆出来，带行号和语法高亮，选中几行就报出「第 N–M 行」。
//
// 读者在代码里指一处，说得出的位置就是行号——芝士改代码时找的也是行号。所以这一档
// 和 markdown 不同：markdown 按「哪一节」说位置，因为正文会重排；源码不重排，行号就是
// 地址。
//
// 两种指法：拖选一段（落在哪几行就是哪几行），或者点行号（点一行选一行，按住 Shift
// 再点一行选中间那一片）。手机上拖选很难停准，点行号是那边的主路。点行号时把那几行
// 真的选中，定位框按选区摆位置（`locatorAnchor`），和拖选走同一条路。
//
// 指过的那几行留一层底色，直到下一次在代码里按下：读者去定位框里打字时，浏览器的选区
// 会跟着焦点走掉，那几行不留个记号就看不出这句话对着哪儿。
//
// 长行默认横向滚，行号那一列钉在左边；「自动换行」由外面那条文件条切（`wrap`）。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { columnsOf, type VisibleColumns, visibleColumns } from './codeColumns'
import { type LineRange, lineRangeIn, selectLines } from './codeSelection'

import { t } from '@/i18n'
import { highlightLines, sourceOfLines } from '@/lib/codeHighlight'

const props = withDefaults(
  defineProps<{
    source: string
    /** highlight.js 的语言名；null 或认不出就按纯文本摆。 */
    language: string | null
    /** 长行折到下一行显示，而不是横向滚。 */
    wrap?: boolean
  }>(),
  { wrap: false }
)

const emit = defineEmits<{
  /** 读者指了几行：起止行号（从 1 数，含两头）和这几行的原文。 */
  lines: [payload: LineRange & { text: string }]
}>()

// 空行塞一个零宽字符：没有字的行撑不起行高，也选不中。
const lines = computed(() => highlightLines(props.source, props.language).map((html) => html || '\u200b'))
// 行号那一列按最大行号的位数留宽：一千多行的文件和十几行的文件，正文都从同一列起。
const digits = computed(() => String(lines.value.length).length)

const root = ref<HTMLElement | null>(null)
const picked = ref<LineRange | null>(null)
let anchorLine: number | null = null

// 横滚时读者在哪一段：见 `codeColumns`。只在真有横滚时露出。
const columns = ref<VisibleColumns | null>(null)

/** 这一份里最长的那一行有多少列。缓存住，滚动时不必每帧重算整份正文。 */
const widest = computed(() => {
  let widest = 0
  for (const line of props.source.split('\n')) widest = Math.max(widest, columnsOf(line, 4))
  return widest
})

function charWidth(): number {
  const scroll = root.value
  if (!scroll) return 0
  const sample = document.createElement('span')
  sample.textContent = '0'.repeat(20)
  sample.style.cssText = 'position:absolute;visibility:hidden;white-space:pre'
  scroll.appendChild(sample)
  const width = sample.getBoundingClientRect().width / 20
  sample.remove()
  return width
}

function measureColumns() {
  const scroll = root.value
  if (!scroll || props.wrap) {
    columns.value = null
    return
  }
  // 只有滚得动才说：一行装得下的时候「第 1–N 列」是废话。
  if (scroll.scrollWidth <= scroll.clientWidth) {
    columns.value = null
    return
  }
  const gutter = scroll.querySelector<HTMLElement>('[data-gutter]')
  columns.value = visibleColumns({
    scrollLeft: scroll.scrollLeft,
    viewWidth: scroll.clientWidth,
    gutterWidth: gutter?.getBoundingClientRect().width ?? 0,
    charWidth: charWidth(),
    total: widest.value,
  })
}

function onScroll() {
  measureColumns()
}

let columnFrame = 0
function scheduleColumns() {
  if (columnFrame) return
  columnFrame = requestAnimationFrame(() => {
    columnFrame = 0
    measureColumns()
  })
}

// 换了正文（或换了折行）就重量一次：列号条说的是「这一份的第几列」，上一份的数留着
// 就是错的。
watch([() => props.source, () => props.wrap], () => scheduleColumns(), { flush: 'post' })

let resize: ResizeObserver | undefined
onMounted(() => {
  scheduleColumns()
  if (typeof ResizeObserver !== 'undefined' && root.value) {
    resize = new ResizeObserver(scheduleColumns)
    resize.observe(root.value)
  }
})
onBeforeUnmount(() => {
  resize?.disconnect()
  if (columnFrame) cancelAnimationFrame(columnFrame)
})

watch(
  () => props.source,
  () => {
    picked.value = null
    anchorLine = null
  }
)

function report(range: LineRange | null) {
  if (!range) return
  picked.value = range
  emit('lines', { ...range, text: sourceOfLines(props.source, range.start, range.end) })
}

function onRelease(event: Event) {
  // 点行号那一下由 `onGutter` 收：这里再读一遍选区，读到的是点下去之前那一段。
  if (event.target instanceof Element && event.target.closest('[data-gutter]')) return
  report(lineRangeIn(root.value, window.getSelection()))
}

function onPress(event: Event) {
  if (event.target instanceof Element && event.target.closest('[data-gutter]')) return
  picked.value = null
}

function onGutter(line: number, event: MouseEvent) {
  const from = event.shiftKey && anchorLine !== null ? anchorLine : line
  if (!event.shiftKey || anchorLine === null) anchorLine = line
  const range = { start: Math.min(from, line), end: Math.max(from, line) }
  if (root.value) selectLines(root.value, range.start, range.end)
  report(range)
}

function isPicked(line: number): boolean {
  const range = picked.value
  return !!range && line >= range.start && line <= range.end
}
</script>

<template>
  <div class="code-view" data-testid="code-view">
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- these read the text selection after it is made, by pointer (mouseup) or by keyboard (Shift+arrows, keyup); they are not controls -->
    <div
      ref="root"
      class="code-view__scroll"
      :class="{ 'code-view__scroll--wrap': wrap }"
      :style="{ '--code-digits': digits }"
      tabindex="0"
      role="group"
      :aria-label="t('work.room.preview.codeView')"
      @mousedown="onPress"
      @mouseup="onRelease"
      @keyup="onRelease"
      @scroll.passive="onScroll"
    >
      <div class="code-view__lines">
        <div
          v-for="(html, index) in lines"
          :key="index"
          class="code-view__line"
          :class="{ 'code-view__line--picked': isPicked(index + 1) }"
          :data-line="index + 1"
        >
          <span class="code-view__ln" data-gutter aria-hidden="true" @click="onGutter(index + 1, $event)">{{
            index + 1
          }}</span>
          <!-- eslint-disable-next-line vue/no-v-html -- highlightLines escapes every piece; only its own spans remain -->
          <span class="code-view__code" data-code v-html="html" />
        </div>
      </div>
    </div>
    <!-- 横滚时读者在哪一段：长行的文件里屏幕上的样子滚到哪儿都一样，没有这个没法说
         出「我在看第几列」。窄面板里字小，它跟着那一档的字号走。 -->
    <div v-if="columns" class="code-view__columns" data-testid="code-columns" aria-live="off">
      {{ t('work.room.preview.columns', { start: columns.start, end: columns.end, total: columns.total }) }}
    </div>
  </div>
</template>

<style scoped>
/* 外面这一层只为了按这一格自己的宽度量窄不窄（容器查询要量的是祖先）。 */
.code-view {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  container-type: inline-size;
}
/* 横滚时那一行坐标：钉在底边，不占正文的地方（它只说明现在看的是哪一段）。 */
.code-view__columns {
  flex: none;
  padding: 2px 10px 3px;
  border-top: 1px solid var(--line);
  background: var(--fill);
  color: var(--muted);
  font-size: 11px;
  line-height: 16px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.code-view__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 8px 0 24px;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--surface);
  tab-size: 4;
}
/* 不换行时整块按最长那一行撑宽，横向滚的是这一块，行号那一列钉在左边。 */
.code-view__lines {
  width: max-content;
  min-width: 100%;
}
.code-view__scroll--wrap .code-view__lines {
  width: 100%;
}
.code-view__line {
  display: flex;
  align-items: flex-start;
}
.code-view__line--picked,
.code-view__line--picked .code-view__ln {
  background: var(--accent-wash);
}
.code-view__ln {
  position: sticky;
  left: 0;
  flex: none;
  box-sizing: content-box;
  width: calc(var(--code-digits) * 1ch);
  padding: 0 12px 0 12px;
  text-align: right;
  color: var(--faint);
  background: var(--surface);
  cursor: pointer;
  user-select: none;
}
.code-view__ln:hover {
  color: var(--muted);
}
.code-view__code {
  flex: 1 1 auto;
  padding-right: 16px;
  white-space: pre;
}
.code-view__scroll--wrap .code-view__code {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@container (max-width: 559px) {
  .code-view__scroll {
    font-size: 12px;
    line-height: var(--lh-12);
  }
  .code-view__ln {
    padding: 0 8px 0 6px;
  }
  .code-view__code {
    padding-right: 12px;
  }
}
</style>
