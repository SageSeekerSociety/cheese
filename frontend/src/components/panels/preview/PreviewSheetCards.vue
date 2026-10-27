<script setup lang="ts">
// 表格的「一行一张卡片」读法：窄处一屏只放得下两三列，一行的十几格得横着滑好几屏才看完。
// 这里把一行竖过来：第一格当卡片的标题，其余每格一行「列名  值」，列名取表的第一行。
//
// 卡片只是换了个摆法，地址不变：点一格照样是那一格（`B7`），评论标记照样挂在那一格上，
// 和表格那一种读法交出去的是同一样东西。
import { t } from '@/i18n'

const props = defineProps<{
  /** 画出来的这些行，第一行当列名。 */
  rows: string[][]
  /** 画出来的列数。 */
  width: number
  /** 每格的地址、显示文字由表格那边给，两种读法说同一套话。 */
  addressAt: (row: number, col: number) => string
  cellText: (value: string) => string
  columnName: (col: number) => string
  /** 选中的那一格。 */
  selected: string
  /** 有评论的格子：地址 → 那几条。 */
  marks: Map<string, { id: string }[]>
  activeMark?: string | null
}>()

const emit = defineEmits<{
  choose: [row: number, col: number]
  mark: [id: string]
}>()

/** 第一行里这一列写的是什么；空着就退回列名（A、B……），总得有个名字。 */
function label(col: number): string {
  const head = (props.rows[0]?.[col] ?? '').trim()
  return head ? props.cellText(head) : props.columnName(col)
}

/** 这一行里有内容的那几格（第一格当标题，不在这里）。全是空的格子不占地方。 */
function fields(row: string[]): number[] {
  const out: number[] = []
  for (let col = 1; col < props.width; col += 1) if ((row[col] ?? '').trim()) out.push(col)
  return out
}
</script>

<template>
  <ol class="ps-cards">
    <template v-for="(row, r) in rows" :key="r">
      <li v-if="r > 0" class="ps-card">
        <button
          type="button"
          class="ps-card__title"
          :class="{ 'is-on': selected === addressAt(r, 0) }"
          @click="emit('choose', r, 0)"
        >
          <span class="ps-card__name">{{ cellText(row[0] ?? '') || label(0) }}</span>
          <span class="ps-card__row t-meta">{{ t('work.room.preview.sheetRow', { row: r + 1 }) }}</span>
        </button>
        <dl class="ps-card__fields">
          <div
            v-for="c in fields(row)"
            :key="c"
            class="ps-card__field"
            :class="{ 'is-on': selected === addressAt(r, c) }"
          >
            <dt class="ps-card__label">{{ label(c) }}</dt>
            <dd class="ps-card__value">
              <button type="button" class="ps-card__cell" @click="emit('choose', r, c)">
                {{ cellText(row[c] ?? '') }}
              </button>
              <button
                v-for="mark in marks.get(addressAt(r, c)) ?? []"
                :key="mark.id"
                type="button"
                class="ps-card__cmt"
                :class="{ 'is-active': mark.id === activeMark }"
                :data-comment-mark="mark.id"
                :aria-label="t('work.room.comments.commentHere')"
                :title="t('work.room.comments.commentHere')"
                @click.stop="emit('mark', mark.id)"
              />
            </dd>
          </div>
        </dl>
      </li>
    </template>
  </ol>
</template>

<style scoped>
.ps-cards {
  flex: 1;
  min-height: 0;
  overflow: auto;
  margin: 0;
  padding: 8px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--canvas);
}

/* 卡片只描边不投影，和预览里的纸同一套层次。 */
.ps-card {
  flex: none;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ps-card__title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  width: 100%;
  padding: 10px 12px 6px;
  text-align: left;
}
.ps-card__name {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
  overflow-wrap: anywhere;
}
.ps-card__row {
  flex: none;
  color: var(--faint);
}

.ps-card__fields {
  margin: 0;
  padding: 0 12px 8px;
}
.ps-card__field {
  display: grid;
  grid-template-columns: minmax(64px, 35%) 1fr;
  gap: 8px;
  padding: 4px 0;
  border-top: 1px solid var(--line);
}
.ps-card__label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.ps-card__value {
  position: relative;
  margin: 0;
  display: flex;
  align-items: flex-start;
  gap: 4px;
}
.ps-card__cell {
  flex: 1;
  min-width: 0;
  text-align: left;
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
  overflow-wrap: anywhere;
  border-radius: var(--radius-sm);
}

/* 选中的那一格：和表格里一样是一圈中性描边，琥珀留给有评论的格子。 */
.ps-card__title.is-on,
.ps-card__field.is-on .ps-card__cell {
  box-shadow: inset 0 0 0 2px var(--ink);
}
.ps-card__title:focus-visible,
.ps-card__cell:focus-visible {
  outline: 2px solid var(--accent);
}

/* 有评论的那一格：值后面一颗琥珀的小点，和表格里挂在格子角上的是同一种。 */
.ps-card__cmt {
  flex: none;
  width: 10px;
  height: 10px;
  margin-top: 6px;
  padding: 0;
  border: 2px solid var(--accent);
  border-radius: 50%;
  background: var(--surface);
  cursor: pointer;
}
.ps-card__cmt.is-active {
  background: var(--accent);
}
</style>
