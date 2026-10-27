<script setup lang="ts">
// 表格的阅读视图。
//
// 表格不转 PDF，这是一个判断而不是省事：分页会把一张表切断、把列甩到下一页，而且
// 排好版之后单元格就不再有地址了。地址恰恰是表格里唯一能指的东西——读者点中 B7，
// 芝士用 openpyxl 打开的也是 B7，两边说的是同一个格子。换成 PDF 就只剩一段文字，
// 要靠找。
//
// 窄处（手机、被挤窄的右栏）一屏只放得下两三列：A 列跟着行号一起钉在左边，横着滑也认得
// 出是哪一行；还可以切成「一行一张卡片」（`PreviewSheetCards`），一行竖着读完。

import type { Sheet } from '@/lib/sheetRead'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import PreviewSheetCards from './PreviewSheetCards.vue'

import { t } from '@/i18n'
import { takeCodePoints } from '@/lib/codePoints'
import { usePreviewNarrow } from '@/lib/previewNarrow'
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
  { kind: 'workbook', marks: () => [], activeMark: null, changed: null, changedBy: '', showBefore: false }
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
const { narrow } = usePreviewNarrow(root)
/** 窄处读成卡片。读者按的，换文件不翻回去；宽回去时照旧是表格（卡片只为窄处而设）。 */
const cards = ref(false)
const showCards = computed(() => narrow.value && cards.value)

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

/** 一片格子摊平之后跟着消息走的那份内容的上限。一片几千行的表不该把消息撑爆；
 *  超出时截断，地址本身照旧指得回整片。 */
const RANGE_TEXT_CHARS = 2000

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
  const read = await readSheets(data, props.kind)
  if (mine !== generation) return
  sheets.value = read.sheets
  sourceText.value = read.sourceText
  endClipped.value = read.endClipped
  failure.value = read.failure
  activeIndex.value = 0
  loading.value = false
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
  <div ref="root" class="ps" :class="{ 'ps--narrow': narrow }">
    <div v-if="loading" class="ps__state">
      <v-progress-circular indeterminate color="primary" size="24" />
    </div>
    <div v-else-if="failure" class="ps__state ps__state--text">
      <v-icon size="28" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
      <div>{{ t('work.room.preview.sheetCantDisplay') }}</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>

    <template v-else-if="active">
      <div v-if="canShowSource || narrow" class="ps__bar">
        <button
          v-if="narrow && !showSource"
          type="button"
          class="ps__toggle"
          :aria-pressed="cards"
          data-testid="sheet-cards"
          @click="cards = !cards"
        >
          <v-icon size="16">{{ cards ? 'mdi-table' : 'mdi-card-text-outline' }}</v-icon>
          {{ cards ? t('work.room.preview.showTable') : t('work.room.preview.sheetCards') }}
        </button>
        <button v-if="canShowSource" type="button" class="ps__toggle" @click="showSource = !showSource">
          <v-icon size="16">{{ showSource ? 'mdi-table' : 'mdi-code-tags' }}</v-icon>
          {{ showSource ? t('work.room.preview.showTable') : t('work.room.preview.showSource') }}
        </button>
      </div>

      <pre v-if="showSource" class="ps__source">{{ sourceText }}</pre>
      <PreviewSheetCards
        v-else-if="showCards"
        :rows="active.rows"
        :width="active.width"
        :address-at="addressAt"
        :cell-text="cellText"
        :column-name="columnName"
        :selected="selected"
        :marks="marksByAddress"
        :active-mark="activeMark"
        @choose="choose"
        @mark="emit('mark', $event)"
      />
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
                :class="{
                  'ps__cell--on': selected === addressAt(r, c - 1),
                  'ps__cell--in': inRange(r, c - 1),
                  'ps__cell--chg': changedTitle(r, c - 1) !== null,
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
                  >{{ cellText(row[c - 1] ?? '') }}</span
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
          <span v-if="changedSheets.has(sheet.name)" class="ps__tab-dot" />
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
  gap: 4px;
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

/* 窄处把 A 列也钉住，贴在行号列右边：行号列定宽，A 列的 left 才有一个确定的数。A 列
   限宽，免得一列长名字占掉大半屏。 */
.ps--narrow .ps__row,
.ps--narrow .ps__corner {
  width: 44px;
  min-width: 44px;
  max-width: 44px;
  box-sizing: border-box;
}
.ps--narrow .ps__col:nth-child(2),
.ps--narrow .ps__cell:nth-child(2) {
  position: sticky;
  left: 44px;
  max-width: 128px;
}
.ps--narrow .ps__cell:nth-child(2) {
  z-index: var(--z-raised);
}
/* 钉住的格子要有底色，否则横滑时后面的格子从它底下透出来；悬停和拖选的底色照旧。 */
.ps--narrow .ps__cell:nth-child(2):not(:hover, .ps__cell--in) {
  background: var(--surface);
}
.ps--narrow .ps__col:nth-child(2) {
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
/* 这一版改过的格子：琥珀浅底加一圈细边，和原型「芝士改过这一格」同一种标法。选中、
   拖过的样式排在它前面声明的会被它盖住，所以它只管底色和细边，不碰粗描边。 */
.ps__cell--chg {
  background: var(--accent-wash);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 55%, var(--surface));
}
.ps__cell--chg:hover {
  background: color-mix(in srgb, var(--accent) 16%, var(--surface));
}
.ps__cell--chg.ps__cell--on {
  box-shadow: inset 0 0 0 2px var(--ink);
}
.ps__cell--chg.ps__cell--in {
  background: var(--selection-bg);
  box-shadow: inset 0 0 0 1px var(--accent);
}
.ps__was {
  margin-right: 6px;
  color: var(--danger-ink);
  text-decoration: line-through;
}
.ps__tab-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-left: 4px;
  border-radius: 50%;
  background: var(--accent);
  vertical-align: middle;
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
