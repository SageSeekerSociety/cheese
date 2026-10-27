<script setup lang="ts">
// 表格的阅读视图。
//
// 表格不转 PDF，这是一个判断而不是省事：分页会把一张表切断、把列甩到下一页，而且
// 排好版之后单元格就不再有地址了。地址恰恰是表格里唯一能指的东西——读者点中 B7，
// 芝士用 openpyxl 打开的也是 B7，两边说的是同一个格子。换成 PDF 就只剩一段文字，
// 要靠找。

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { t } from '@/i18n'
import { takeCodePoints } from '@/lib/codePoints'
import { cellDisplay } from '@/lib/sheetCells'

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
    /** 现在打开的是哪一条：它那一格画得显眼些。 */
    activeMark?: string | null
  }>(),
  { kind: 'workbook', marks: () => [], activeMark: null }
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

type Sheet = {
  name: string
  /** 画出来的这些行（最多 `MAX_ROWS` 行、每行最多 `MAX_COLS` 格）。 */
  rows: string[][]
  /** 画出来的列数。 */
  width: number
  /** 整张表本来有多少行 / 多少列 —— 只画了一部分时要靠它说清。 */
  totalRows: number
  totalCols: number
  /** 这一张表里有格子被截短了。按表记，不按文件记：多表工作簿里切到没有长格的那张
   *  表，不该还挂着「较长的单元格被截短了」。 */
  shortened: boolean
}

const sheets = ref<Sheet[]>([])
const activeIndex = ref(0)
const loading = ref(false)
const failure = ref('')
const selected = ref<string>('')

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

/** 此刻高亮的那一片：拖的时候跟着手走，拖完了留着，让读者看见自己选了哪儿。 */
const markedRange = computed(() => {
  const start = dragStart.value
  const end = dragEnd.value
  if (!start || !end) return null
  const row1 = Math.min(start.row, end.row)
  const row2 = Math.max(start.row, end.row)
  const col1 = Math.min(start.col, end.col)
  const col2 = Math.max(start.col, end.col)
  if (row1 === row2 && col1 === col2) return null
  return { row1, row2, col1, col2 }
})

function inRange(row: number, col: number): boolean {
  const range = markedRange.value
  if (!range) return false
  return row >= range.row1 && row <= range.row2 && col >= range.col1 && col <= range.col2
}

/** 画出来的上限。一张几十万行的 CSV 整份铺进 DOM 会把面板拖死，而人要找的那几格
 *  总在开头。超出的部分不是丢了，是没画出来 —— 所以下面那条提示必须说清「只显示了
 *  前 N 行 M 列」，并且文件条上照旧有「下载原文件」这条路。 */
const MAX_ROWS = 500
const MAX_COLS = 64
const MAX_CELL = 300
/** 一片格子摊平之后跟着消息走的那份内容的上限。一片几千行的表不该把消息撑爆；
 *  超出时截断，地址本身照旧指得回整片。 */
const RANGE_TEXT_CHARS = 2000
/** 解析之前先切字节的上限：一份 200 MB 的 CSV 光解码成字符串就先占掉几百 MB。 */
const MAX_BYTES = 1 << 20

/** 字节在解析之前就被切了，表尾可能缺。 */
const endClipped = ref(false)

/** 看原文还是看表格。分隔符猜错、编码认错时，原文是唯一的自救路径。 */
const showSource = ref(false)
const sourceText = ref('')

let generation = 0

const active = computed<Sheet | null>(() => sheets.value[activeIndex.value] ?? null)

/** 一格在这一版里的地址：列名 + 行号，和工作表、和 `openpyxl` 用的是同一套。 */
function addressAt(rowIndex: number, colIndex: number): string {
  return `${columnName(colIndex)}${rowIndex + 1}`
}

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
    const active = on.filter((m) => m.id === props.activeMark)
    if (active.length) byAddress.set(address, [...on.filter((m) => m.id !== props.activeMark), ...active])
  }
  return byAddress
})

/** 0→A、25→Z、26→AA：电子表格的列名，和 openpyxl 用的是同一套。 */
function columnName(index: number): string {
  let name = ''
  let n = index
  while (n >= 0) {
    name = String.fromCharCode((n % 26) + 65) + name
    n = Math.floor(n / 26) - 1
  }
  return name
}

/** CSV 的编码取决于谁写的。芝士 写 UTF-8；而人从 Excel 导出的 CSV 在中文 Windows
 *  上是 GBK，按 UTF-8 解出来是一整片乱码，而且不抛错 —— 所以先严格按 UTF-8 解，
 *  它失败了才说明这不是 UTF-8，退到 GBK。 */
function decodeText(bytes: Uint8Array): string {
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    try {
      text = new TextDecoder('gbk').decode(bytes)
    } catch {
      text = new TextDecoder('utf-8').decode(bytes)
    }
  }
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
}

/** 分隔符不总是逗号：Excel 按系统的列表分隔符导出，某些区域设置下是分号；从数据库
 *  导出的常是制表符。数第一行里哪个多就用哪个。引号里的分隔符也被数进去了，但一份
 *  真表里真正的分隔符仍然占多数，而猜错的表现是整张表挤成一列 —— 一眼就看得出。 */
function sniffSeparator(text: string): string {
  const end = text.search(/\r?\n/)
  const line = end === -1 ? text : text.slice(0, end)
  let best = ','
  let most = 0
  for (const sep of [',', ';', '\t']) {
    const n = line.split(sep).length - 1
    if (n > most) {
      best = sep
      most = n
    }
  }
  return best
}

/** 按 RFC 4180 读：引号里的分隔符、换行和 `""` 转义的引号都不是边界。自己写而不是
 *  按行 split，因为一个带地址或备注的单元格里就有逗号和换行，split 会把一行拆散，
 *  而拆散之后地址全错 —— 而地址是这个视图存在的理由。 */
function parseCsv(text: string, sep: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]
    if (quoted) {
      if (ch !== '"') field += ch
      else if (text[i + 1] === '"') {
        field += '"'
        i += 1
      } else quoted = false
      continue
    }
    if (ch === '"') quoted = true
    else if (ch === sep) {
      row.push(field)
      field = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1
      row.push(field)
      field = ''
      rows.push(row)
      row = []
    } else field += ch
  }
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

/** 老版 `.xls`（Excel 97-2003）是 OLE2 复合文档，头八个字节固定是这几个；OOXML 是
 *  zip（`PK`）。exceljs 只读 OOXML，拿 OLE2 进去只会抛一句「找不到中央目录」——
 *  按魔数先认出来，才能把话说成人话。 */
const OLE2_MAGIC = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]

function isOle2(bytes: Uint8Array): boolean {
  return bytes.length >= OLE2_MAGIC.length && OLE2_MAGIC.every((b, i) => bytes[i] === b)
}

/** 切到 `limit` 处可能正好把一个多字节字符切成两半。残字节会让严格 UTF-8 解码抛错，
 *  于是一份合法的 UTF-8 文件被判成 GBK、整份乱码 —— 先把切点退到字符边界上（UTF-8
 *  的续字节长这样 `10xxxxxx`），切出来的前缀才是这份文件的一个真前缀。 */
function sliceAtCharBoundary(bytes: Uint8Array, limit: number): Uint8Array {
  if (bytes.length <= limit) return bytes
  let end = limit
  while (end > 0 && (bytes[end] & 0xc0) === 0x80) end -= 1
  return bytes.subarray(0, end)
}

/** CSV / TSV 只有一张表，而且它没有名字 —— 没有名字就不该编一个：地址栏里写 `B7`，
 *  和一个真有工作表名的 `Sheet1!B7` 是两种不同的坐标，编出来的名字会让读者以为
 *  这份文件里还有别的表。 */
function openText(data: ArrayBuffer, kind: 'csv' | 'tsv') {
  const bytes = new Uint8Array(data)
  // 先切字节再解码：整份读进来再切开，切之前那几百 MB 已经占住了。
  endClipped.value = bytes.length > MAX_BYTES
  const text = decodeText(sliceAtCharBoundary(bytes, MAX_BYTES))
  sourceText.value = text
  // TSV 的分隔符是格式的一部分，不用猜；CSV 才要数第一行里哪个符号多。
  const rows = parseCsv(text, kind === 'tsv' ? '\t' : sniffSeparator(text))
  if (!rows.length) {
    failure.value = t('work.room.preview.fileEmpty')
    return
  }
  // 不用 Math.max(...)：一份只由换行组成的 1 MB 文件有几十万行，摊开成实参会把栈压爆。
  let totalCols = 0
  for (const r of rows) if (r.length > totalCols) totalCols = r.length
  const kept = rows.slice(0, MAX_ROWS).map((r) => r.slice(0, MAX_COLS))
  sheets.value = [
    {
      name: '',
      rows: kept,
      width: Math.min(totalCols, MAX_COLS),
      totalRows: rows.length,
      totalCols,
      shortened: kept.some((r) => r.some((c) => c.length > MAX_CELL)),
    },
  ]
  activeIndex.value = 0
}

async function open(data: ArrayBuffer) {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  sheets.value = []
  selected.value = ''
  // 换了一份文件，上一份上圈的那一片不该跟着过来：坐标在另一张表里另有意义。
  dragging.value = false
  dragStart.value = null
  dragEnd.value = null
  sourceText.value = ''
  endClipped.value = false
  // 换一份文件就回到表格：上一个是 csv、这个不是，留在原文视图会看见一片空白，
  // 而且那条切换按钮在这种文件上根本不出现，读者没有路回去。
  showSource.value = false
  try {
    if (props.kind === 'ods') {
      // OpenDocument 表格和 OOXML 不是一个格式，exceljs 读不了它。与其让它抛一句
      // 读不懂的错，不如说清这条路走不通、以及可以怎么拿到内容。
      failure.value = t('work.room.preview.sheetFormatUnsupported')
      return
    }
    if (props.kind === 'csv' || props.kind === 'tsv') {
      openText(data, props.kind)
      return
    }
    if (isOle2(new Uint8Array(data))) {
      failure.value = t('work.room.preview.sheetLegacyUnsupported')
      return
    }
    const ExcelJS = await import('exceljs')
    const book = new ExcelJS.Workbook()
    await book.xlsx.load(data.slice(0))
    if (mine !== generation) return
    const read: Sheet[] = []
    book.eachSheet((worksheet) => {
      const rows: string[][] = []
      let width = 0
      // 一张表里有没有超长的格子，是这张表自己的事：一本表里既有小台账又有长备注，
      // 用同一个标记说「被截短了」，切到没有长格子的那页也会顶着这句提示。
      let shortened = false
      worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
        if (rowNumber > MAX_ROWS) return
        const cells: string[] = []
        row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
          if (colNumber > MAX_COLS) return
          const text = cellDisplay(cell.value)
          if (text.length > MAX_CELL) shortened = true
          cells[colNumber - 1] = text
        })
        width = Math.max(width, cells.length)
        rows[rowNumber - 1] = cells
      })
      for (let i = 0; i < rows.length; i += 1) if (!rows[i]) rows[i] = []
      read.push({
        name: worksheet.name,
        rows,
        width,
        totalRows: worksheet.rowCount,
        totalCols: worksheet.columnCount,
        shortened,
      })
    })
    if (mine !== generation) return
    sheets.value = read
    activeIndex.value = 0
    if (!read.length) failure.value = t('work.room.preview.noSheets')
  } catch (e) {
    if (mine !== generation) return
    failure.value = e instanceof Error ? e.message : t('work.room.preview.sheetReadFailed')
  } finally {
    if (mine === generation) loading.value = false
  }
}

/** 画出来的是截短版，发出去的仍是整格内容 —— 地址对的是这一格，不是它显示成什么样。 */
function cellText(value: string): string {
  return value.length > MAX_CELL ? `${value.slice(0, MAX_CELL)}…` : value
}

/** 底部那条提示。只写要人知道的那几件，一件都没有就不占地方。 */
const notice = computed(() => {
  const sheet = active.value
  if (!sheet) return ''
  const parts: string[] = []
  if (sheet.totalRows > sheet.rows.length || sheet.totalCols > sheet.width) {
    parts.push(t('work.room.preview.sheetClipped', { rows: sheet.rows.length, cols: sheet.width }))
  }
  if (sheet.shortened) parts.push(t('work.room.preview.sheetCellsShortened'))
  if (endClipped.value) parts.push(t('work.room.preview.sheetEndMissing'))
  return parts.join(' ')
})

/** 分隔文本才有「原文」可看。工作簿的字节是压缩包，ods 我们根本读不开，两者都
 *  没有一条能读的原文可切。 */
const canShowSource = computed(() => props.kind === 'csv' || props.kind === 'tsv')

function choose(rowIndex: number, colIndex: number) {
  const sheet = active.value
  if (!sheet) return
  const address = addressAt(rowIndex, colIndex)
  selected.value = address
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
  selected.value = ''
  dragging.value = false
  dragStart.value = null
  dragEnd.value = null
}

/** 按下：这一片从这一格起。 */
function startDrag(rowIndex: number, colIndex: number) {
  dragging.value = true
  dragStart.value = { row: rowIndex, col: colIndex }
  dragEnd.value = { row: rowIndex, col: colIndex }
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
onMounted(() => window.addEventListener('mouseup', endDrag))
onBeforeUnmount(() => window.removeEventListener('mouseup', endDrag))

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
</script>

<template>
  <div class="ps">
    <div v-if="loading" class="ps__state">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div v-else-if="failure" class="ps__state ps__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.sheetCantDisplay') }}</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>

    <template v-else-if="active">
      <div v-if="canShowSource" class="ps__bar">
        <button type="button" class="ps__toggle" @click="showSource = !showSource">
          <v-icon size="16">{{ showSource ? 'mdi-table' : 'mdi-code-tags' }}</v-icon>
          {{ showSource ? t('work.room.preview.showTable') : t('work.room.preview.showSource') }}
        </button>
      </div>

      <pre v-if="showSource" class="ps__source">{{ sourceText }}</pre>
      <div v-else class="ps__grid">
        <table class="ps__table">
          <thead>
            <tr>
              <th class="ps__corner" />
              <th v-for="c in active.width" :key="c" class="ps__col">{{ columnName(c - 1) }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, r) in active.rows" :key="r">
              <th class="ps__row">{{ r + 1 }}</th>
              <!-- eslint-disable-next-line vuejs-accessibility/mouse-events-have-key-events -- 拖过一片是鼠标手势（按下、经过、松开）。键盘上没有对应的一步，和页上点一处、圈一块同一条。 -->
              <td
                v-for="c in active.width"
                :key="c"
                class="ps__cell"
                :class="{ 'ps__cell--on': selected === addressAt(r, c - 1), 'ps__cell--in': inRange(r, c - 1) }"
                @mousedown="startDrag(r, c - 1)"
                @mouseenter="extendDrag(r, c - 1)"
                @click="choose(r, c - 1)"
              >
                <span class="ps__text">{{ cellText(row[c - 1] ?? '') }}</span>
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
          </tbody>
        </table>
      </div>

      <div v-if="!showSource && notice" class="ps__note">{{ notice }}</div>
      <div v-else-if="showSource && endClipped" class="ps__note">
        {{ t('work.room.preview.sheetEndMissing') }}
      </div>

      <div v-if="sheets.length > 1" class="ps__tabs">
        <button
          v-for="(sheet, i) in sheets"
          :key="sheet.name"
          type="button"
          class="ps__tab"
          :class="{ 'ps__tab--on': i === activeIndex }"
          @click="switchSheet(i)"
        >
          {{ sheet.name }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ps {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface);
}

.ps__state {
  padding: 32px 16px;
  color: var(--muted);
}
.ps__state--text {
  text-align: center;
}

.ps__bar {
  flex: none;
  display: flex;
  justify-content: flex-end;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line);
}

.ps__toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--muted);
}
.ps__toggle:hover {
  background: var(--fill);
  color: var(--ink);
}

/* 原文就是原文：等宽、不折行、原样保留空白和换行 —— 读者切过来就是为了看清分隔符和
   编码到底长什么样，这里再排版一次反而把要找的东西抹掉了。 */
.ps__source {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 12px;
  overflow: auto;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre;
  tab-size: 8;
}

.ps__note {
  flex: none;
  padding: 6px 12px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--muted);
}

.ps__grid {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.ps__table {
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
  color: var(--text);
}

/* 行号列和列名行在滚动时留在原地——一张宽表滚到第 40 列还认得出是哪一行。 */
.ps__col,
.ps__row,
.ps__corner {
  position: sticky;
  background: var(--fill);
  color: var(--muted);
  font-weight: 500;
  font-size: 12px;
  text-align: center;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 4px 8px;
  white-space: nowrap;
}
.ps__col {
  top: 0;
  z-index: var(--z-raised);
}
.ps__row {
  left: 0;
  z-index: var(--z-raised);
  min-width: 44px;
}
.ps__corner {
  top: 0;
  left: 0;
  z-index: var(--z-raised-2);
}

.ps__cell {
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 4px 8px;
  min-width: 96px;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
  position: relative;
}
.ps__cell:hover {
  background: var(--fill);
}
/* 文字自己截断，不靠格子的 overflow：格子改成相对定位之后，标记要能露在格子边上。 */
.ps__text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 选中的格子用一圈中性描边，不用琥珀：琥珀留给真正有评论的那几格。
   这也正好是电子表格本来的样子——一个粗边框，不是一块底色。 */
.ps__cell--on {
  box-shadow: inset 0 0 0 2px var(--ink);
}
/* 拖过的那一片：底色铺满，四边各描一道，让读者看见自己选了哪儿。 */
.ps__cell--in {
  background: var(--selection-bg);
  box-shadow: inset 0 0 0 1px var(--accent);
}
/* 有人在那一格上评过：右上角一颗琥珀的小点，点得到（评论栏靠它打开）。和页上那几颗
   同一种形状，只是挂在格子的角上——表格里一格的坐标本来就是一个地址，没有「页内比例」
   那回事。 */
.ps__cmt {
  position: absolute;
  top: 1px;
  right: 1px;
  width: 10px;
  height: 10px;
  padding: 0;
  border: 2px solid var(--accent);
  border-radius: 50%;
  background: var(--surface);
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.ps__cmt:hover,
.ps__cmt.is-active {
  background: var(--accent);
}

.ps__tabs {
  flex: none;
  display: flex;
  gap: 4px;
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  overflow-x: auto;
}

.ps__tab {
  flex: none;
  padding: 4px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--muted);
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.ps__tab:hover {
  background: var(--fill);
}
.ps__tab--on {
  background: var(--fill-2);
  color: var(--ink);
}
</style>
