<script setup lang="ts">
// 表格的阅读视图。
//
// 表格不转 PDF，这是一个判断而不是省事：分页会把一张表切断、把列甩到下一页，而且
// 排好版之后单元格就不再有地址了。地址恰恰是表格里唯一能指的东西——读者点中 B7，
// 芝士用 openpyxl 打开的也是 B7，两边说的是同一个格子。换成 PDF 就只剩一段文字，
// 要靠找。
//
// 所以这一份要做的，是一个读表的人真正会用的那几件：
//   - 顶部一条地址栏：此刻指着哪一格（拖过一片就是 `C3:E8`），以及那一格里写的是什么
//     （有公式就写公式）。表格里说不清「哪一格」就等于说不清要改哪一处。
//   - 文件自己带的样子：数字按它自己的格式排（`386,200`、`4.1%`），该靠右的靠右，
//     文件里加粗的表头照旧加粗。读者认的是他打开 Excel 时看见的那张表。
//   - 几千行也翻得到底：只把看得见的那一屏铺进 DOM（`ROW` 那几条），拖动和点格子
//     照旧。另有一条定位：输地址跳过去，输几个字找内容。
//   - 键盘：方向键走格子，Shift+方向键扩一片，回车把这一格说出去。鼠标能做的指认，
//     不用鼠标也做得了。
//
// 窄处（手机、被挤窄的右栏）一屏只放得下两三列：A 列跟着行号一起钉在左边，横着滑也认得
// 出是哪一行；还可以切成「一行一张卡片」（`PreviewSheetCards`），一行竖着读完。

import type { RawChart, SheetChart } from '@/lib/sheetChart'
import type { CellAlign, Sheet } from '@/lib/sheetRead'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import PreviewSheetCards from './PreviewSheetCards.vue'
import PreviewSheetChart from './PreviewSheetChart.vue'

import { t } from '@/i18n'
import { takeCodePoints } from '@/lib/codePoints'
import { usePreviewNarrow } from '@/lib/previewNarrow'
import { resolveCharts } from '@/lib/sheetChart'
import { columnName, MAX_CELL, readSheets } from '@/lib/sheetRead'

const props = withDefaults(
  defineProps<{
    /** 表格文件的原始字节。 */
    data: ArrayBuffer | null
    /** 这份字节是什么。CSV/TSV 是一串文本，工作簿是一个 zip，读法没有一处相同，
     *  而字节本身看不出区别 —— 后缀只有调用方知道（`lib/fileKind.sheetKindOf`）。 */
    kind?: 'workbook' | 'csv' | 'tsv' | 'ods'
    /** 这一份文件上已有的评论指着的那几格：工作表名 + A1 地址（`sheet-cell` 的形状）。
     *  地址在这一版里落不到任何一格上时自然不画（行列变了、表被删了）。 */
    marks?: readonly { id: string; sheet: string; address: string }[]
    /** 攒在待发篮里、还没发出去的那几处：格子上挂一颗带序号的 pin，和 tray 上那一行
     *  对得上。`n` 是它在篮里的编号。 */
    pins?: readonly { id: string; n: number; sheet: string; address: string }[]
    /** 现在打开的是哪一条：它那一格画得显眼些。 */
    activeMark?: string | null
    /** 这一版改过的那几格（「表名!地址」→ 上一版那里写的什么）：画上底色，悬停说原来是什么。
     *  不给就不画（没有在比、或者不是房间里有历史的文件）。 */
    changed?: ReadonlyMap<string, string> | null
    /** 改了这几格的是谁：悬停那一句「某某改过这一格」里的名字。 */
    changedBy?: string
    /** 对比态：改过的格子里把原来的值也写出来（删除线），不用悬停就看得到改了什么。 */
    showBefore?: boolean
  }>(),
  {
    kind: 'workbook',
    marks: () => [],
    pins: () => [],
    activeMark: null,
    changed: null,
    changedBy: '',
    showBefore: false,
  }
)

const emit = defineEmits<{
  /** 读者点了一个格子，带上它的地址和当前的值。 */
  (e: 'cell', payload: { address: string; value: string; sheet: string }): void
  /** 读者拖过一片格子：两个角的 A1 地址，加上这片格子里摊平的内容。
   *
   *  和单格一样，地址是文件本身给得出来的坐标；受话人拿两个角就回到了那一片。
   *  `start` 是左上、`end` 是右下。 */
  (e: 'range', payload: { sheet: string; start: string; end: string; text: string }): void
  /** 读者点了某一格上的评论标记：把那一串评论打开。 */
  (e: 'mark', id: string): void
}>()

const root = ref<HTMLElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const locateInput = ref<HTMLInputElement | null>(null)
const { narrow } = usePreviewNarrow(root)
/** 窄处读成卡片。读者按的，换文件不翻回去；宽回去时照旧是表格（卡片只为窄处而设）。 */
const cards = ref(false)
const showCards = computed(() => narrow.value && cards.value)

const sheets = ref<Sheet[]>([])
const activeIndex = ref(0)
const loading = ref(false)
const failure = ref('')
const selected = ref<string>('')

/** 键盘走到的那个格子（行、列都从 0 起）。鼠标点一格也把它挪过去：地址栏说的是同一格。 */
const focus = ref<{ row: number; col: number } | null>(null)

/** 拖着选一片格子时两个角的坐标（行、列都是 0 起的下标）；没在拖就是 null。
 *
 *  表格里指一片和指一格是同一个手势的两种长度：按下、松开在同一格上是「这一格」，
 *  拖过别的格子就是「这一片」。`end` 跟着经过的格子走，所以从右下往左上拖也对——
 *  发出去之前两个角会摆回左上、右下。 */
const dragStart = ref<{ row: number; col: number } | null>(null)
const dragEnd = ref<{ row: number; col: number } | null>(null)
/** 鼠标还按着没有。松开之后两个角要留着（读者得看见自己选了哪一片），但那一瞬间
 *  手势就已经结束了：再跟着指针走，画的就不是刚才发出去的那一片了。 */
const dragging = ref(false)

/** 按下时那一格还只是一个格子：松手仍在原地就是「点了一格」，交出去的是单格。 */
function rectOf(start: { row: number; col: number }, end: { row: number; col: number }) {
  return {
    row1: Math.min(start.row, end.row),
    row2: Math.max(start.row, end.row),
    col1: Math.min(start.col, end.col),
    col2: Math.max(start.col, end.col),
  }
}

/** 此刻高亮的那一片：拖的时候跟着手走，拖完了留着，让读者看见自己选了哪儿。 */
const markedRange = computed(() => {
  const start = dragStart.value
  const end = dragEnd.value
  if (!start || !end) return null
  const range = rectOf(start, end)
  if (range.row1 === range.row2 && range.col1 === range.col2) return null
  return range
})

/** 地址栏说的那一片：正在拖（或拖完还留着）就是那一片，否则是键盘/鼠标指着的这一格。 */
const shownRange = computed(() => {
  const marked = markedRange.value
  if (marked) return marked
  const single = focus.value
  if (!single) return null
  return { row1: single.row, row2: single.row, col1: single.col, col2: single.col }
})

function inRange(row: number, col: number): boolean {
  const range = markedRange.value
  if (!range) return false
  return row >= range.row1 && row <= range.row2 && col >= range.col1 && col <= range.col2
}

/** 一片格子摊平之后跟着消息走的那份内容的上限。一片几千行的表不该把消息撑爆；
 *  超出时截断，地址本身照旧指得回整片。 */
const RANGE_TEXT_CHARS = 2000

/** 字节在解析之前就被切了，表尾可能缺。 */
const endClipped = ref(false)

/** 看原文还是看表格。分隔符猜错、编码认错时，原文是唯一的自救路径。 */
const showSource = ref(false)
const sourceText = ref('')

/** 看图还是看表。工作簿里带图时，底部多一颗「图表」：图是文件的一部分，读者打不开它
 *  就只能下载原件。 */
const showCharts = ref(false)
/** 这份工作簿里的图（还没取数）。分隔文本没有图。 */
const charts = ref<RawChart[]>([])
/** 读到了、我们画不出来的图有几张，按工作表名记。 */
const chartsSkipped = ref<Map<string, number>>(new Map())

let generation = 0

const active = computed<Sheet | null>(() => sheets.value[activeIndex.value] ?? null)

/** 一格在这一版里的地址：列名 + 行号，和工作表、和 `openpyxl` 用的是同一套。 */
function addressAt(rowIndex: number, colIndex: number): string {
  return `${columnName(colIndex)}${rowIndex + 1}`
}

// ---- 地址栏 ----

/** 地址栏左边那个格子写什么：一章表里有好几张表时带上表名，不然 `B7` 说的是哪张表的 B7
 *  说不清。一片就写两个角（`C3:E8`）。还没指任何一处时是一横，不是空着——空着看起来
 *  像还没读完。 */
const addressLabel = computed(() => {
  const sheet = active.value
  const range = shownRange.value
  if (!sheet || !range) return '—'
  const start = addressAt(range.row1, range.col1)
  const end = addressAt(range.row2, range.col2)
  const address = start === end ? start : `${start}:${end}`
  return sheets.value.length > 1 && sheet.name ? `${sheet.name}!${address}` : address
})

/** 地址栏右边那一格里写的是什么：有公式写公式（读者一眼看得出这一格是算出来的），
 *  否则写它显示的字。空格子给空串，不要一句「（空）」把公式栏挤满。 */
const contentLabel = computed(() => {
  const sheet = active.value
  const range = shownRange.value
  if (!sheet || !range) return ''
  const address = addressAt(range.row1, range.col1)
  const formula = sheet.formulas?.get(address)
  if (formula) return formula
  return cellText(sheet.rows[range.row1]?.[range.col1] ?? '')
})

// ---- 大表的窗口 ----

/** 一屏铺多少行、铺哪一段。几千行全铺进 DOM，滚动一次重排一遍，翻到底之前就卡了；
 *  表里每一行都是一行字（`white-space: nowrap`），行高是常量，所以窗口按行号切就够，
 *  上下各留几条做缓冲，滚起来看不出跳。 */
const OVERSCAN = 8
const rowHeight = ref(29)
const headHeight = ref(30)
const scrollTop = ref(0)
const viewHeight = ref(0)

const totalRows = computed(() => active.value?.rows.length ?? 0)
/** 量不到高度时（还没上屏、或者测试环境里没有布局）按一屏默认高算：窗口要比一整张表
 *  大，不能因为量不到就把表画成空白的。 */
const measuredHeight = computed(() => (viewHeight.value > 0 ? viewHeight.value : 600))
const windowStart = computed(() =>
  Math.max(0, Math.floor((scrollTop.value - headHeight.value) / rowHeight.value) - OVERSCAN)
)
const windowEnd = computed(() => {
  const end = (scrollTop.value + measuredHeight.value - headHeight.value) / rowHeight.value + OVERSCAN
  return Math.min(totalRows.value, Math.max(windowStart.value, Math.ceil(end)))
})
/** 画出来的那几行，带上它们在整张表里的行号（窗口外的不在 DOM 里）。 */
const visibleRows = computed(() =>
  (active.value?.rows ?? [])
    .slice(windowStart.value, windowEnd.value)
    .map((cells, i) => ({ cells, row: windowStart.value + i }))
)
const padTop = computed(() => windowStart.value * rowHeight.value)
const padBottom = computed(() => Math.max(0, (totalRows.value - windowEnd.value) * rowHeight.value))

/** 卡片那一档先铺多少行。一行一张卡片、每张都带自己的若干字段，几千行一次铺进 DOM
 *  不比表格省；和表格那一档同一个做法——先铺一段，到底了再往后接。 */
const CARD_CHUNK = 60
const cardRows = ref(CARD_CHUNK)
/** 这一批从第几行起。平常是 0；跳到很后面时才把窗口挪过去（见 `revealRow`），
 *  这样既看得到目标，又不会为了一次跳转把几千张卡片铺进 DOM。 */
const cardStart = ref(0)
const cardRowsShown = computed(() => active.value?.rows.slice(cardStart.value, cardStart.value + cardRows.value) ?? [])
const moreCards = computed(() => cardStart.value + cardRows.value < (active.value?.rows.length ?? 0))

/** 行高、表头高在第一次画出来之后量一次；换了字号、缩放了页面时要再量。 */
function measure() {
  const grid = scroller.value
  if (!grid) return
  viewHeight.value = grid.clientHeight
  const head = grid.querySelector('thead')
  if (head) headHeight.value = head.getBoundingClientRect().height
  const cell = grid.querySelector('tbody tr.ps__row-line td')
  if (cell) {
    const height = cell.getBoundingClientRect().height
    if (height > 0) rowHeight.value = height
  }
}

let ticking = false
function onScroll() {
  if (ticking) return
  ticking = true
  requestAnimationFrame(() => {
    ticking = false
    if (scroller.value) scrollTop.value = scroller.value.scrollTop
  })
}

/** 把某一行滚到看得见的地方。表头是钉住的，它压在表格顶上，所以目标位置要加上表头高。 */
function scrollToRow(row: number) {
  const grid = scroller.value
  if (!grid) return
  const top = headHeight.value + row * rowHeight.value
  const bottom = top + rowHeight.value
  if (top < grid.scrollTop + headHeight.value) grid.scrollTop = Math.max(0, top - headHeight.value)
  else if (bottom > grid.scrollTop + grid.clientHeight) grid.scrollTop = bottom - grid.clientHeight
  scrollTop.value = grid.scrollTop
}

let observer: ResizeObserver | null = null
/** 观察器当前盯着的那一个滚动区。换一个就先把旧的摘掉：滚动区每次换表、切卡片/图表
 *  都是新元素，旧的留在观察器上会被一直量着。 */
let observed: HTMLElement | null = null

// ---- 定位 ----

const locate = ref('')
const locateMiss = ref(false)

/** 地址写法：`B7`、`B7:D9`、`数据!B7`、`数据!B7:D9`。地址栏自己就把一片写成
 *  `C3:E8`，读者把它抄回定位框得找得到同一个地方。 */
function parseAddress(text: string): { sheet: string | null; start: string; end: string } | null {
  const match = /^([^!]+!)?(\$?[A-Za-z]{1,3}\$?\d{1,7})(?::(\$?[A-Za-z]{1,3}\$?\d{1,7}))?$/.exec(text.trim())
  if (!match) return null
  const bare = (value: string) => value.replace(/\$/g, '').toUpperCase()
  return {
    sheet: match[1] ? match[1].slice(0, -1) : null,
    start: bare(match[2]),
    end: bare(match[3] ?? match[2]),
  }
}

/** `C3` 这种写法拆成行列（都从 0 起）；拆不出来给 null。 */
function splitAddress(address: string): { row: number; col: number } | null {
  const match = /^([A-Za-z]{1,3})(\d{1,7})$/.exec(address)
  if (!match) return null
  let col = 0
  for (const ch of match[1].toUpperCase()) col = col * 26 + (ch.charCodeAt(0) - 64)
  return { row: Number(match[2]) - 1, col: col - 1 }
}

/** 地图上走一格：这是键盘那一路的落点，地址栏跟着它变。 */
function moveTo(row: number, col: number) {
  const sheet = active.value
  if (!sheet) return
  const lastRow = Math.max(0, sheet.rows.length - 1)
  const lastCol = Math.max(0, sheet.width - 1)
  const at = { row: Math.min(Math.max(0, row), lastRow), col: Math.min(Math.max(0, col), lastCol) }
  focus.value = at
  selected.value = ''
  dragStart.value = null
  dragEnd.value = null
  revealRow(at.row)
  scrollToRow(at.row)
}

/** 卡片那一档只有一个窗口，没有滚动区可 scrollTo。跳到窗口之外（输地址、找内容）时
 *  把窗口挪到那一行附近；不是把窗口一路拉长到那一行——`A4999` 会因此一次铺出五千张
 *  卡片，正是分块要避免的。 */
function revealRow(row: number) {
  if (!showCards.value) return
  const from = cardStart.value
  if (row >= from && row < from + cardRows.value) return
  cardStart.value = Math.max(0, row - Math.floor(CARD_CHUNK / 2))
  cardRows.value = CARD_CHUNK
}

/** 回车/空格：把键盘指着的这一格说出去（和点一下那一格是同一件事）。 */
function commitFocus(target: { row: number; col: number } | null = focus.value) {
  const sheet = active.value
  const at = target
  if (!sheet || !at) return
  const address = addressAt(at.row, at.col)
  selected.value = address
  emit('cell', { address, value: sheet.rows[at.row]?.[at.col] ?? '', sheet: sheet.name })
}

/** 输入框里按下回车：像地址就跳过去，不像地址就当内容找。 */
function runLocate() {
  const query = locate.value.trim()
  locateMiss.value = false
  if (!query) return
  const at = parseAddress(query)
  if (at) {
    const index = at.sheet
      ? sheets.value.findIndex((sheet) => sheet.name.toLowerCase() === at.sheet!.toLowerCase())
      : -1
    if (at.sheet && index < 0) {
      locateMiss.value = true
      return
    }
    if (index >= 0 && index !== activeIndex.value) switchSheet(index)
    const sheet = active.value
    const start = splitAddress(at.start)
    const end = splitAddress(at.end)
    // `inside` 要等 `sheet` 判完非空才用得上，所以先判空再定义它。
    if (!sheet || !start || !end) {
      locateMiss.value = true
      return
    }
    const inside = (corner: { row: number; col: number }) => corner.row < sheet.rows.length && corner.col < sheet.width
    if (!inside(start) || !inside(end)) {
      locateMiss.value = true
      return
    }
    if (start.row === end.row && start.col === end.col) {
      moveTo(start.row, start.col)
      return
    }
    // 一片：和拖出来的一样看待，两个角都摆上，地址栏写回 `C3:E8`。两个角都先夹过了——
    // 越界的角不进这一支，否则地址栏会写出 `A1:Z999` 这种表里没有的坐标，回车交出去的
    // 也是一格不存在的格子。
    focus.value = end
    selected.value = ''
    dragStart.value = start
    dragEnd.value = end
    revealRow(start.row)
    scrollToRow(start.row)
    return
  }
  // 内容：当前这张表里第一个包含它的格子。找的是「显示出来的那几个字」，所以读者拿
  // 屏幕上看到的东西就能找回来。
  const sheet = active.value
  if (!sheet) return
  const needle = query.toLowerCase()
  for (let row = 0; row < sheet.rows.length; row += 1) {
    const cells = sheet.rows[row]
    for (let col = 0; col < sheet.width; col += 1) {
      if ((cells[col] ?? '').toLowerCase().includes(needle)) {
        moveTo(row, col)
        return
      }
    }
  }
  locateMiss.value = true
}

/** `Ctrl/Cmd+F` 把焦点交给定位框：读表的习惯在那儿。 */
function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
    event.preventDefault()
    locateInput.value?.focus()
    locateInput.value?.select()
    return
  }
  const at = focus.value ?? { row: 0, col: 0 }
  const page = Math.max(1, Math.floor((viewHeight.value - headHeight.value) / rowHeight.value) - 1)
  const sheet = active.value
  if (!sheet) return
  const lastRow = Math.max(0, sheet.rows.length - 1)
  const lastCol = Math.max(0, sheet.width - 1)
  const extend = event.shiftKey
  const anchor = dragStart.value ?? at
  function go(row: number, col: number) {
    event.preventDefault()
    if (extend) {
      dragging.value = false
      dragStart.value = anchor
      dragEnd.value = { row: Math.min(Math.max(0, row), lastRow), col: Math.min(Math.max(0, col), lastCol) }
      focus.value = dragEnd.value
      scrollToRow(dragEnd.value.row)
      return
    }
    moveTo(row, col)
  }
  switch (event.key) {
    case 'ArrowUp':
      return go(at.row - 1, at.col)
    case 'ArrowDown':
    case 'Enter':
      if (event.key === 'Enter' && !extend) {
        event.preventDefault()
        commitFocus(at)
        return
      }
      return go(at.row + 1, at.col)
    case 'ArrowLeft':
      return go(at.row, at.col - 1)
    case 'ArrowRight':
      return go(at.row, at.col + 1)
    case 'Home':
      return go(at.row, 0)
    case 'End':
      return go(at.row, lastCol)
    case 'PageUp':
      return go(at.row - page, at.col)
    case 'PageDown':
      return go(at.row + page, at.col)
    case 'Escape':
      event.preventDefault()
      focus.value = null
      selected.value = ''
      dragStart.value = null
      dragEnd.value = null
      return
    case ' ':
      event.preventDefault()
      commitFocus(at)
      return
  }
}

// ---- 读表 ----

/** 正在看的这张表上那些评论标记，按地址索引。
 *
 *  按工作表名先筛一道：地址只在它自己那张表里有意义，一本表里好几张表都可能有 `B7`。
 *  地址在这一版里落不到任何一格上时（表更短了、或者那一格没画出来）自然不画 ——
 *  和页上那几颗同一条，查看器不猜它挪到哪去了。
 *
 *  同一格上有多条时都摆出来（各自的圆点挨在一起），最后一个是正打开的那条：它画得
 *  显眼、也压在最上面，点到的就是读者正看着的那一条。 */
const marksByAddress = computed(() => {
  const name = active.value?.name ?? ''
  const byAddress = new Map<string, { id: string }[]>()
  for (const mark of props.marks ?? []) {
    if (mark.sheet !== name) continue
    const on = byAddress.get(mark.address.toUpperCase())
    if (on) on.push({ id: mark.id })
    else byAddress.set(mark.address.toUpperCase(), [{ id: mark.id }])
  }
  for (const [address, on] of byAddress) {
    const activeMark = on.filter((m) => m.id === props.activeMark)
    if (activeMark.length) byAddress.set(address, [...on.filter((m) => m.id !== props.activeMark), ...activeMark])
  }
  return byAddress
})

/** 待发批注的序号 pin：格子上挂一颗带编号的，和 tray 里那一行对得上。 */
const pinsByAddress = computed(() => {
  const name = active.value?.name ?? ''
  const byAddress = new Map<string, { id: string; n: number }[]>()
  for (const pin of props.pins ?? []) {
    if (pin.sheet !== name) continue
    const on = byAddress.get(pin.address.toUpperCase())
    if (on) on.push({ id: pin.id, n: pin.n })
    else byAddress.set(pin.address.toUpperCase(), [{ id: pin.id, n: pin.n }])
  }
  return byAddress
})

async function open(data: ArrayBuffer) {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  sheets.value = []
  selected.value = ''
  focus.value = null
  cardRows.value = CARD_CHUNK
  cardStart.value = 0
  // 换了一份文件，上一份上圈的那一片不该跟着过来：坐标在另一张表里另有意义。
  dragging.value = false
  dragStart.value = null
  dragEnd.value = null
  sourceText.value = ''
  endClipped.value = false
  locate.value = ''
  locateMiss.value = false
  showCharts.value = false
  scrollTop.value = 0
  // 换一份文件就回到表格：上一个是 csv、这个不是，留在原文视图会看见一片空白，
  // 而且那条切换按钮在这种文件上根本不出现，读者没有路回去。
  showSource.value = false
  const read = await readSheets(data, props.kind)
  if (mine !== generation) return
  sheets.value = read.sheets
  charts.value = read.charts ?? []
  chartsSkipped.value = read.chartsSkipped ?? new Map()
  sourceText.value = read.sourceText
  endClipped.value = read.endClipped
  failure.value = read.failure
  activeIndex.value = 0
  loading.value = false
  if (scroller.value) scroller.value.scrollTop = 0
  await nextTick()
  measure()
}

/** 这一格这一版改过没有；改过就给上一版那里写的什么（原来是空的就是空串）。 */
function beforeAt(rowIndex: number, colIndex: number): string | undefined {
  const sheet = active.value
  return sheet ? props.changed?.get(`${sheet.name}!${addressAt(rowIndex, colIndex)}`) : undefined
}

/** 改过的格子悬停时那一句：谁改的、原来是什么。 */
function changedTitle(rowIndex: number, colIndex: number): string | null {
  const before = beforeAt(rowIndex, colIndex)
  if (before === undefined) return null
  return before
    ? t('work.room.versions.cellChanged', { who: props.changedBy, before: cellText(before) })
    : t('work.room.versions.cellChangedFromEmpty', { who: props.changedBy })
}

/** 正在看的这张表上的图。图挂在它自己那张表上（Excel 里就是浮在那张表的画布上）。 */
function chartsFor(sheet: Sheet | null | undefined): SheetChart[] {
  if (!sheet || !charts.value.length) return []
  return resolveCharts(charts.value, sheets.value).get(sheet.name) ?? []
}
const activeCharts = computed<SheetChart[]>(() => chartsFor(active.value))
/** 这一张表上有几张图我们画不出来：底部说一句，别让它悄悄消失。 */
const skippedCharts = computed(() => (active.value ? chartsSkipped.value.get(active.value.name) ?? 0 : 0))

/** 看图 / 回到表。卡片那一档在窄处替掉表格，所以点「图表」时先把卡片关掉——不然
 *  按钮按下去什么都不会变（卡片那一支排在前面，赢的是它）。 */
function toggleCharts() {
  showCharts.value = !showCharts.value
  if (showCharts.value) cards.value = false
}

/** 反过来也一样：卡片开着时「图表」那颗开关的按下状态要跟着落下，不然它亮着而屏幕上
 *  是卡片，再点一下只是把那颗灯关掉、什么都没变。 */
function toggleCards() {
  cards.value = !cards.value
  if (cards.value) showCharts.value = false
}

/** 哪几张表里有改过的格子：页签上点一颗点，读者知道该切过去看哪张。 */
const changedSheets = computed(() => {
  const names = new Set<string>()
  for (const key of props.changed?.keys() ?? []) names.add(key.slice(0, key.lastIndexOf('!')))
  return names
})

/** 画出来的是截短版，发出去的仍是整格内容 —— 地址对的是这一格，不是它显示成什么样。 */
function cellText(value: string): string {
  return value.length > MAX_CELL ? `${value.slice(0, MAX_CELL)}…` : value
}

/** 这一格怎么摆：文件里写了就照它，没写就（数靠右）由读表那一步给过默认值。 */
function alignAt(rowIndex: number, colIndex: number): CellAlign | undefined {
  return active.value?.aligns?.[rowIndex]?.[colIndex]
}

/** 这一格文件里加粗没有（表头、合计行常靠它认出来）。 */
function boldAt(rowIndex: number, colIndex: number): boolean {
  return active.value?.bold?.has(addressAt(rowIndex, colIndex)) ?? false
}

/** 底部那一行：这一张表一共多少行，以及只画了一部分时缺的是什么。 */
const summary = computed(() => {
  const sheet = active.value
  if (!sheet) return ''
  const parts: string[] = []
  if (sheet.totalRows > sheet.rows.length || sheet.totalCols > sheet.width) {
    parts.push(t('work.room.preview.sheetClipped', { rows: sheet.rows.length, cols: sheet.width }))
  } else {
    parts.push(t('work.room.preview.sheetRows', { rows: sheet.rows.length }))
  }
  if (sheet.shortened) parts.push(t('work.room.preview.sheetCellsShortened'))
  if (endClipped.value) parts.push(t('work.room.preview.sheetEndMissing'))
  return parts.join(' · ')
})

/** 分隔文本才有「原文」可看。工作簿的字节是压缩包，ods 我们根本读不开，两者都
 *  没有一条能读的原文可切。 */
const canShowSource = computed(() => props.kind === 'csv' || props.kind === 'tsv')

function choose(rowIndex: number, colIndex: number) {
  const sheet = active.value
  if (!sheet) return
  const address = addressAt(rowIndex, colIndex)
  selected.value = address
  focus.value = { row: rowIndex, col: colIndex }
  dragStart.value = null
  dragEnd.value = null
  emit('cell', {
    address,
    value: sheet.rows[rowIndex]?.[colIndex] ?? '',
    sheet: sheet.name,
  })
}

/** 换一张工作表：上一张表上圈的那一片、点的那一格都不带过去，`B2:D4` 在另一张表里
 *  是另外几格。 */
function switchSheet(index: number) {
  activeIndex.value = index
  cardRows.value = CARD_CHUNK
  cardStart.value = 0
  // 换到一张没有图的表：图表那一支要退出去。不退的话它照旧赢，而「图表 N」那颗开关
  // 因为这张表没图已经不画了，读者就再也回不到表格。
  if (!chartsFor(sheets.value[index]).length) showCharts.value = false
  selected.value = ''
  focus.value = null
  dragging.value = false
  dragStart.value = null
  dragEnd.value = null
  scrollTop.value = 0
  if (scroller.value) scroller.value.scrollTop = 0
}

/** 按下：这一片从这一格起。 */
function startDrag(rowIndex: number, colIndex: number) {
  dragging.value = true
  dragStart.value = { row: rowIndex, col: colIndex }
  dragEnd.value = { row: rowIndex, col: colIndex }
  focus.value = { row: rowIndex, col: colIndex }
}

/** 经过一格：正按着的时候这一片的另一个角跟着走。松开之后路过别的格子不改它——
 *  那一片已经说出去了，改的是屏幕上的画，对不上刚才发的那一份。 */
function extendDrag(rowIndex: number, colIndex: number) {
  if (!dragging.value) return
  dragEnd.value = { row: rowIndex, col: colIndex }
}

/** 松开：真拖过别的格子才发这一片；按在同一点上是单击，交给 `choose`。
 *
 *  挂在 `window` 上而不是格子上：拖到最后一行、最后一列时，手很可能是在表外面松开
 *  的 —— 只挂在格子上，那一下就白拖了。`window` 上这个也会收到表内的松开（冒泡），
 *  所以格子上不必再挂一个。 */
function endDrag() {
  if (!dragging.value) return
  dragging.value = false
  const range = markedRange.value
  const sheet = active.value
  if (!range || !sheet) {
    dragStart.value = null
    dragEnd.value = null
    return
  }
  const start = addressAt(range.row1, range.col1)
  const end = addressAt(range.row2, range.col2)
  // 摊平这一片的内容：一行一行地接，行与行之间用换行、格与格之间用制表符——和从表格
  // 里复制出来的那一份一样，受话人一眼看得见里面是什么。
  //
  // 一边接一边数，够了就停：上限不能等整片拼完再截。格子里存的是整格（只有画出来时才
  // 截短），一本几十万格的表按整片拼出来就是几百 MB，为一个跟着消息走的摘要不值当。
  // 数的是码点（`lib/codePoints.ts`，和指针那几处同一条），一个 emoji 在下标上占两格，
  // 按码点算既对得上「两千字」这个说法，也不会把它劈成半个。
  const parts: string[] = []
  let used = 0
  /** 再接一段；接得下整段就接整段，接不下只接开头那一点。够长了答 false。 */
  function append(piece: string): boolean {
    const taken = takeCodePoints(piece, RANGE_TEXT_CHARS - used)
    parts.push(taken.text)
    used += taken.points
    return taken.complete
  }
  outer: for (let row = range.row1; row <= range.row2; row += 1) {
    if (row > range.row1 && !append('\n')) break
    for (let col = range.col1; col <= range.col2; col += 1) {
      if (col > range.col1 && !append('\t')) break outer
      if (!append(sheet.rows[row]?.[col] ?? '')) break outer
    }
  }
  emit('range', { sheet: sheet.name, start, end, text: parts.join('') })
  selected.value = ''
  // 两个角留着：松手之后读者还得看得见自己刚选的是哪一片，和单击留下的那个框一样。
  // 下一次按下、或者点某一格（`choose`）时才换掉。
}

// 松开的那一下可能在表外面：挂在 `window` 上才收得到。挂一次、卸组件时摘掉。
onMounted(() => {
  window.addEventListener('mouseup', endDrag)
  // 老一点的浏览器（和测试环境）可能没有 ResizeObserver：没有它也要能用，只是面板
  // 改尺寸之后要等下一次重新渲染才重新量。
  if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(() => measure())
})

/** 观察滚动区：它是读完之后才渲染出来的，所以 `onMounted` 那一刻还没有这个元素；等它
 *  出现再挂。`watch` 而不是 `onUpdated`——后者每次滚动（窗口跟着换行、DOM 重排）都会
 *  跑一次，`measure()` 里的 `clientHeight` 就变成每帧一次强制重排，正好是窗口化要省掉的。
 *  只在元素真的换了的时候量：同一个元素不重挂、也不重量。 */
watch(
  scroller,
  (grid) => {
    if (grid === observed) return
    if (observed) observer?.unobserve(observed)
    observed = grid
    if (!grid) return
    observer?.observe(grid)
    measure()
  },
  { flush: 'post' }
)
onBeforeUnmount(() => {
  window.removeEventListener('mouseup', endDrag)
  observer?.disconnect()
  observer = null
})

watch(
  [() => props.data, () => props.kind],
  ([data]) => {
    if (data) void open(data)
    else {
      generation += 1
      sheets.value = []
    }
  },
  { immediate: true }
)

watch(showSource, async () => {
  await nextTick()
  measure()
})
</script>

<template>
  <!-- 表格里拖出一片、点一格都是在「指」：批注框直接出卡，不先出「添加批注」（PanelPreviewView 认这个标记）。 -->
  <div ref="root" class="ps" :class="{ 'ps--narrow': narrow }" data-locator-points>
    <div v-if="loading" class="ps__state">
      <!-- 读一份几千行的工作簿要几百毫秒到几秒，先画出表的骨架：读者看见的是一张正在
           长出来的表，而不是一个转着圈的空白。 -->
      <div class="ps__skeleton" aria-hidden="true">
        <div v-for="i in 6" :key="i" class="ps__skeleton-row" />
      </div>
      <v-progress-circular indeterminate color="primary" size="20" />
    </div>
    <div v-else-if="failure" class="ps__state ps__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.sheetCantDisplay') }}</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>

    <template v-else-if="active">
      <div class="ps__bar">
        <!-- 地址栏：此刻指着哪一格、那一格里写的是什么。这是「说清楚要改哪一处」的落点，
             所以它一直在，不随窄处收起。 -->
        <span class="ps__addr" data-testid="sheet-address" :title="addressLabel">{{ addressLabel }}</span>
        <span class="ps__value" :title="contentLabel">{{ contentLabel }}</span>
        <input
          ref="locateInput"
          v-model="locate"
          class="ps__locate"
          type="text"
          :class="{ 'ps__locate--miss': locateMiss }"
          :placeholder="t('work.room.preview.sheetLocate')"
          :aria-label="t('work.room.preview.sheetLocate')"
          autocomplete="off"
          data-testid="sheet-locate"
          @keydown.enter.prevent="runLocate"
          @input="locateMiss = false"
        />
        <button
          v-if="narrow && !showSource"
          type="button"
          class="ps__toggle"
          :aria-pressed="cards"
          data-testid="sheet-cards"
          @click="toggleCards"
        >
          <v-icon size="16">{{ cards ? 'mdi-table' : 'mdi-card-text-outline' }}</v-icon>
          {{ cards ? t('work.room.preview.showTable') : t('work.room.preview.sheetCards') }}
        </button>
        <button
          v-if="canShowSource"
          type="button"
          class="ps__toggle"
          :aria-pressed="showSource"
          @click="showSource = !showSource"
        >
          <v-icon size="16">{{ showSource ? 'mdi-table' : 'mdi-code-tags' }}</v-icon>
          {{ showSource ? t('work.room.preview.showTable') : t('work.room.preview.showSource') }}
        </button>
      </div>

      <pre v-if="showSource" class="ps__source">{{ sourceText }}</pre>
      <template v-else-if="showCards">
        <PreviewSheetCards
          :rows="cardRowsShown"
          :row-offset="cardStart"
          :width="active.width"
          :address-at="addressAt"
          :cell-text="cellText"
          :column-name="columnName"
          :selected="selected"
          :marks="marksByAddress"
          :pins="pinsByAddress"
          :active-mark="activeMark"
          :sheet-name="active.name"
          :changed="changed"
          :changed-by="changedBy"
          :show-before="showBefore"
          @choose="choose"
          @mark="emit('mark', $event)"
        />
        <!-- 到底了就往后接一段：几千行的表在窄处一屏一屏读，不该一次全铺进 DOM。 -->
        <button
          v-if="moreCards"
          type="button"
          class="ps__more"
          data-testid="sheet-more"
          @click="cardRows += CARD_CHUNK"
        >
          {{ t('work.room.preview.sheetMore', { shown: cardStart + cardRowsShown.length, total: active.rows.length }) }}
        </button>
      </template>
      <div v-else-if="showCharts" class="ps__charts">
        <PreviewSheetChart v-for="(chart, i) in activeCharts" :key="i" :chart="chart" :narrow="narrow" />
      </div>
      <!-- `.ps__grid` 只是滚动的那一层；「可以走动的表格」是里面那张表：`role="grid"`、
           键盘落点和方向键都挂在它上面（和 `AdminFeedbackTable` 同一种写法）。方向键在格
           子里走，读屏听到的是「某某表第几行第几列」。
           `aria-rowcount` 说的是整张表有多少行——DOM 里只铺看得见的那一屏，不写的话读屏
           会以为表就这么长。 -->
      <div v-else ref="scroller" class="ps__grid" @scroll="onScroll">
        <table
          class="ps__table"
          role="grid"
          :aria-rowcount="totalRows + 1"
          :aria-colcount="active.width"
          :aria-label="t('work.room.preview.sheetRegion')"
          tabindex="0"
          @keydown="onKeydown"
        >
          <thead>
            <tr>
              <th class="ps__corner" />
              <th v-for="c in active.width" :key="c" class="ps__col" scope="col">{{ columnName(c - 1) }}</th>
            </tr>
          </thead>
          <tbody>
            <!-- 窗口上面那一截：不画，但占着高度，滚动条的长度和整张表对得上。 -->
            <tr v-if="padTop" class="ps__spacer" aria-hidden="true" role="presentation">
              <td :colspan="active.width + 1" :style="{ height: `${padTop}px` }" />
            </tr>
            <tr v-for="{ cells, row: r } in visibleRows" :key="r" class="ps__row-line" :aria-rowindex="r + 2">
              <th class="ps__row" scope="row">{{ r + 1 }}</th>
              <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events -- 拖过一片是鼠标手势（按下、经过、松开）。键盘上对应的一步是方向键选格子再回车（见 onKeydown），不是对每一格都挂 keydown。 -->
              <td
                v-for="c in active.width"
                :key="c"
                class="ps__cell"
                :class="{
                  'ps__cell--on': selected === addressAt(r, c - 1),
                  'ps__cell--focus':
                    focus && focus.row === r && focus.col === c - 1 && selected !== addressAt(r, c - 1),
                  'ps__cell--in': inRange(r, c - 1),
                  'ps__cell--chg': changedTitle(r, c - 1) !== null,
                  'ps__cell--bold': boldAt(r, c - 1),
                  'ps__cell--right': alignAt(r, c - 1) === 'right',
                  'ps__cell--center': alignAt(r, c - 1) === 'center',
                }"
                :title="changedTitle(r, c - 1) ?? undefined"
                @mousedown="startDrag(r, c - 1)"
                @mouseenter="extendDrag(r, c - 1)"
                @click="choose(r, c - 1)"
              >
                <span class="ps__text"
                  ><del v-if="showBefore && beforeAt(r, c - 1)" class="ps__was">{{
                    cellText(beforeAt(r, c - 1) ?? '')
                  }}</del
                  >{{ cellText(cells[c - 1] ?? '') }}</span
                >
                <!-- 待发批注：一颗带序号的 pin，编号和 tray 里那一行对得上——读者看表就知道
                     「第 2 条说的是这一格」。摆在格子右上角外面，压着格线，和原型那一颗同一种。 -->
                <span
                  v-for="pin in pinsByAddress.get(addressAt(r, c - 1)) ?? []"
                  :key="`pin-${pin.id}`"
                  class="ps__pin"
                  :data-pending-pin="pin.id"
                  :title="t('work.room.preview.pendingPin', { n: pin.n })"
                  aria-hidden="true"
                  >{{ pin.n }}</span
                >
                <!-- Comment marks: one dot per thread whose sheet-cell lands on this cell.
                     Drawn inside the cell so it moves with the cell when the table
                     re-renders. -->
                <button
                  v-for="(mark, i) in marksByAddress.get(addressAt(r, c - 1)) ?? []"
                  :key="mark.id"
                  type="button"
                  class="ps__cmt"
                  :class="{ 'is-active': mark.id === activeMark }"
                  :data-comment-mark="mark.id"
                  :style="{ right: `${1 + i * 11}px` }"
                  :aria-label="t('work.room.comments.commentHere')"
                  :title="t('work.room.comments.commentHere')"
                  @click.stop="emit('mark', mark.id)"
                />
              </td>
            </tr>
            <!-- 窗口下面那一截。 -->
            <tr v-if="padBottom" class="ps__spacer" aria-hidden="true" role="presentation">
              <td :colspan="active.width + 1" :style="{ height: `${padBottom}px` }" />
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 底部一行：工作表页签在左，整张表多大在右。原型把这两样摆在同一条上，
           读者扫一眼就知道下面还有几张表、这张表有多长。 -->
      <div v-if="!showSource" class="ps__foot">
        <div v-if="sheets.length > 1" class="ps__tabs" role="tablist">
          <button
            v-for="(sheet, i) in sheets"
            :key="sheet.name"
            type="button"
            role="tab"
            class="ps__tab"
            :class="{ 'ps__tab--on': i === activeIndex }"
            :aria-selected="i === activeIndex"
            @click="switchSheet(i)"
          >
            {{ sheet.name }}
            <span v-if="changedSheets.has(sheet.name)" class="ps__tab-dot" />
          </button>
        </div>
        <span v-else class="ps__tab-single">{{ active.name }}</span>
        <button
          v-if="activeCharts.length"
          type="button"
          class="ps__tab ps__chart-tab"
          :class="{ 'ps__tab--on': showCharts }"
          :aria-pressed="showCharts"
          data-testid="sheet-charts"
          @click="toggleCharts"
        >
          <v-icon size="14">mdi-chart-bar</v-icon>
          {{ t('work.room.preview.sheetChartCount', { count: activeCharts.length }) }}
        </button>
        <span v-if="skippedCharts" class="ps__skipped" data-testid="sheet-charts-skipped">{{
          t('work.room.preview.sheetChartsSkipped', { count: skippedCharts })
        }}</span>
        <span class="ps__summary" :class="{ 'ps__summary--miss': locateMiss }">{{
          locateMiss ? t('work.room.preview.sheetLocateMiss') : summary
        }}</span>
      </div>
      <div v-if="showSource && endClipped" class="ps__note">
        {{ t('work.room.preview.sheetEndMissing') }}
      </div>
    </template>
  </div>
</template>

<style scoped src="./previewSheet.css"></style>
