<script setup lang="ts">
// 一份 Office 文件的两个版本，按读者的单位比：Word 按段落、表格按单元格、幻灯片按页。
//
// Word 两栏并排，左边是之前那一版，右边是现在这一版，同一段落在同一行；改过的段落里
// 删掉的字和加上的字各自标出来。字一样、只改了格式的段落列在最下面，「只改了格式」
// 说得出改在哪。表格列出变了的格子和它原来的值，写明变的是公式还是数值。幻灯片按
// 阅读顺序排开，标出改过的、新加的和删掉的页。
//
// 「改动」和产物页用的是这一个组件。
import type { CompareRow, OfficeComparison } from '@/types/reviewComment'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ comparison: OfficeComparison }>()

const changedSheets = computed(() => (props.comparison.sheets ?? []).filter((s) => s.status !== 'same'))
const slides = computed(() => props.comparison.slides ?? [])

function sideText(row: CompareRow, side: 'before' | 'after'): { op: string; text: string }[] {
  if (row.op === 'changed') {
    const hide = side === 'before' ? 'insert' : 'delete'
    return (row.pieces ?? []).filter((p) => p.op !== hide)
  }
  if (row.op === 'same') return [{ op: 'equal', text: row.text ?? '' }]
  if (row.op === 'removed') return side === 'before' ? [{ op: 'delete', text: row.text ?? '' }] : []
  return side === 'after' ? [{ op: 'insert', text: row.text ?? '' }] : []
}

const slideTag: Record<string, string> = {
  changed: 'work.room.review.slideChanged',
  added: 'work.room.review.slideAdded',
  removed: 'work.room.review.slideRemoved',
}
</script>

<template>
  <div class="office-compare">
    <p v-if="comparison.new_file" class="t-meta office-compare__note">{{ t('work.room.review.compareNew') }}</p>
    <p v-else-if="comparison.identical" class="t-meta office-compare__note">
      {{ t('work.room.review.compareSame') }}
    </p>

    <!-- Word：两栏，同一段落同一行。 -->
    <template v-if="comparison.kind === 'word' && !comparison.new_file">
      <div class="office-compare__cols office-compare__head">
        <span>{{ t('work.room.review.compareBefore') }}</span>
        <span>{{ t('work.room.review.compareAfter') }}</span>
      </div>
      <div
        v-for="(row, i) in comparison.rows ?? []"
        :key="i"
        class="office-compare__cols office-compare__row"
        :class="`office-compare__row--${row.op}`"
      >
        <p class="office-compare__para">
          <span v-for="(p, j) in sideText(row, 'before')" :key="j" :class="`office-compare__${p.op}`">{{
            p.text
          }}</span>
        </p>
        <p class="office-compare__para">
          <span v-for="(p, j) in sideText(row, 'after')" :key="j" :class="`office-compare__${p.op}`">{{ p.text }}</span>
        </p>
      </div>
      <p v-if="comparison.truncated" class="t-meta office-compare__note">
        {{ t('work.room.review.compareTruncated') }}
      </p>
      <div v-if="comparison.formatting?.length" class="office-compare__formatting">
        <div class="office-compare__title">
          {{ t('work.room.review.compareFormatting', { n: comparison.formatting.length }) }}
        </div>
        <ul>
          <li v-for="f in comparison.formatting" :key="f.after">
            {{ t('work.room.review.paragraph', { n: f.after }) }} · {{ f.text }}
          </li>
        </ul>
      </div>
    </template>

    <!-- 表格：变了的格子，原来的值写在旁边。 -->
    <template v-else-if="comparison.kind === 'sheet'">
      <section v-for="sheet in changedSheets" :key="sheet.name" class="office-compare__sheet">
        <div class="office-compare__title">
          {{ sheet.name }} ·
          <template v-if="sheet.status === 'added'">{{ t('work.room.review.slideAdded') }}</template>
          <template v-else-if="sheet.status === 'removed'">{{ t('work.room.review.slideRemoved') }}</template>
          <template v-else>{{ t('work.room.review.sheetChanged', { n: sheet.cells.length }) }}</template>
        </div>
        <table v-if="sheet.cells.length" class="office-compare__cells">
          <thead>
            <tr>
              <th>{{ t('work.room.review.cell') }}</th>
              <th>{{ t('work.room.review.compareBefore') }}</th>
              <th>{{ t('work.room.review.compareAfter') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in sheet.cells" :key="c.address">
              <td class="office-compare__address">{{ c.address }}</td>
              <td>
                <span class="office-compare__delete">{{ c.before }}</span>
                <code v-if="c.formula && c.before_formula" class="office-compare__formula"
                  >={{ c.before_formula }}</code
                >
              </td>
              <td>
                <span class="office-compare__insert">{{ c.after }}</span>
                <code v-if="c.formula && c.after_formula" class="office-compare__formula">={{ c.after_formula }}</code>
                <span v-if="c.formula" class="office-compare__tag">{{ t('work.room.review.formula') }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>

    <!-- 幻灯片：按阅读顺序，改过的、新加的、删掉的各有标记。 -->
    <template v-else-if="comparison.kind === 'slide'">
      <ol class="office-compare__slides">
        <li v-for="(s, i) in slides" :key="i" class="office-compare__slide" :class="`office-compare__slide--${s.op}`">
          <div class="office-compare__slide-head">
            <span class="t-meta">{{ t('work.room.review.page', { n: s.after ?? s.before }) }}</span>
            <span class="office-compare__slide-title">{{ s.title }}</span>
            <span v-if="slideTag[s.op]" class="office-compare__tag">{{ t(slideTag[s.op]) }}</span>
          </div>
          <p v-if="s.op === 'changed'" class="office-compare__para">
            <span v-for="(p, j) in s.pieces ?? []" :key="j" :class="`office-compare__${p.op}`">{{ p.text }}</span>
          </p>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.office-compare {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
}
.office-compare__note {
  margin: 0;
}
.office-compare__cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
.office-compare__head {
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.office-compare__row {
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
}
.office-compare__para {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}
.office-compare__delete {
  background: var(--danger-wash);
  color: var(--danger-ink);
  text-decoration: line-through;
}
.office-compare__insert {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.office-compare__title {
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
}
.office-compare__formatting ul {
  margin: 4px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.office-compare__cells {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: var(--lh-13);
}
.office-compare__cells th,
.office-compare__cells td {
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
.office-compare__cells th {
  color: var(--muted);
  font-weight: 400;
}
.office-compare__address {
  font-family: var(--font-mono);
}
.office-compare__formula {
  display: block;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 12px;
}
.office-compare__tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--fill);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}
.office-compare__slides {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.office-compare__slide {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.office-compare__slide--removed {
  opacity: 0.6;
}
.office-compare__slide-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.office-compare__slide-title {
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
