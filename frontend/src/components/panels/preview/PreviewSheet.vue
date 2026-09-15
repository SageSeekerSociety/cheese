<script setup lang="ts">
// 表格的阅读视图。
//
// 表格不转 PDF，这是一个判断而不是省事：分页会把一张表切断、把列甩到下一页，而且
// 排好版之后单元格就不再有地址了。地址恰恰是表格里唯一能指的东西——读者点中 B7，
// 芝士用 openpyxl 打开的也是 B7，两边说的是同一个格子。换成 PDF 就只剩一段文字，
// 要靠找。

import { computed, ref, watch } from 'vue'

const props = defineProps<{
  /** 表格文件的原始字节。 */
  data: ArrayBuffer | null
}>()

const emit = defineEmits<{
  /** 读者点了一个格子，带上它的地址和当前的值。 */
  (e: 'cell', payload: { address: string; value: string; sheet: string }): void
}>()

type Sheet = { name: string; rows: string[][]; width: number }

const sheets = ref<Sheet[]>([])
const activeIndex = ref(0)
const loading = ref(false)
const failure = ref('')
const selected = ref<string>('')

let generation = 0

const active = computed<Sheet | null>(() => sheets.value[activeIndex.value] ?? null)

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

/** 单元格里可能是数字、日期、公式结果或富文本，屏幕上要的都是它显示出来的样子。 */
function display(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>
    if (typeof v.text === 'string') return v.text
    if (Array.isArray(v.richText)) {
      return (v.richText as { text?: string }[]).map((r) => r.text ?? '').join('')
    }
    if ('result' in v) return display(v.result)
    if ('hyperlink' in v && typeof v.text === 'string') return v.text
    return ''
  }
  return String(value)
}

async function open(data: ArrayBuffer) {
  const mine = ++generation
  loading.value = true
  failure.value = ''
  sheets.value = []
  selected.value = ''
  try {
    const ExcelJS = await import('exceljs')
    const book = new ExcelJS.Workbook()
    await book.xlsx.load(data.slice(0))
    if (mine !== generation) return
    const read: Sheet[] = []
    book.eachSheet((worksheet) => {
      const rows: string[][] = []
      let width = 0
      worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
        const cells: string[] = []
        row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
          cells[colNumber - 1] = display(cell.value)
        })
        width = Math.max(width, cells.length)
        rows[rowNumber - 1] = cells
      })
      for (let i = 0; i < rows.length; i += 1) if (!rows[i]) rows[i] = []
      read.push({ name: worksheet.name, rows, width })
    })
    if (mine !== generation) return
    sheets.value = read
    activeIndex.value = 0
    if (!read.length) failure.value = '这个表格里没有工作表'
  } catch (e) {
    if (mine !== generation) return
    failure.value = e instanceof Error ? e.message : '无法读取这个表格'
  } finally {
    if (mine === generation) loading.value = false
  }
}

function choose(rowIndex: number, colIndex: number) {
  const sheet = active.value
  if (!sheet) return
  const address = `${columnName(colIndex)}${rowIndex + 1}`
  selected.value = address
  emit('cell', {
    address,
    value: sheet.rows[rowIndex]?.[colIndex] ?? '',
    sheet: sheet.name,
  })
}

watch(
  () => props.data,
  (data) => {
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
      <div>无法显示这个表格</div>
      <div class="t-meta mt-1">{{ failure }}</div>
    </div>

    <template v-else-if="active">
      <div class="ps__grid">
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
              <td
                v-for="c in active.width"
                :key="c"
                class="ps__cell"
                :class="{ 'ps__cell--on': selected === `${columnName(c - 1)}${r + 1}` }"
                @click="choose(r, c - 1)"
              >
                {{ row[c - 1] ?? '' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="sheets.length > 1" class="ps__tabs">
        <button
          v-for="(sheet, i) in sheets"
          :key="sheet.name"
          type="button"
          class="ps__tab"
          :class="{ 'ps__tab--on': i === activeIndex }"
          @click="activeIndex = i"
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
  z-index: 1;
}
.ps__row {
  left: 0;
  z-index: 1;
  min-width: 44px;
}
.ps__corner {
  top: 0;
  left: 0;
  z-index: 2;
}

.ps__cell {
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  padding: 4px 8px;
  min-width: 96px;
  max-width: 320px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: background-color 0.12s ease;
}
.ps__cell:hover {
  background: var(--fill);
}
/* 选中的格子用一圈中性描边，不用琥珀：琥珀留给这一格里唯一的主操作（发送）。
   这也正好是电子表格本来的样子——一个粗边框，不是一块底色。 */
.ps__cell--on {
  box-shadow: inset 0 0 0 2px var(--ink);
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
