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
  /** 这一张表的名字：改过的格子按「表名!地址」记，和表格那边同一个键。 */
  sheetName?: string
  /** 这一版改过的那几格（「表名!地址」→ 上一版那里写的什么）：和表格里一样标底色，
   *  悬停说原来是什么。不给就不画。 */
  changed?: ReadonlyMap<string, string> | null
  /** 改了这几格的是谁。 */
  changedBy?: string
  /** 对比态：把原来的值也用删除线写出来。 */
  showBefore?: boolean
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

/** 这一行里有内容的那几格（第一格当标题，不在这里）。全是空的格子不占地方 —— 但这一版
 *  刚被改空的格子要留下：删掉的东西也得看得见，不然卡片上那一处改动凭空消失了。 */
function fields(row: string[], rowIndex: number): number[] {
  const out: number[] = []
  for (let col = 1; col < props.width; col += 1) {
    if ((row[col] ?? '').trim() || beforeAt(rowIndex, col) !== undefined) out.push(col)
  }
  return out
}

/** 这一格这一版改过没有；改过就给上一版那里写的什么（原来是空的就是空串）。 */
function beforeAt(row: number, col: number): string | undefined {
  return props.changed?.get(`${props.sheetName ?? ''}!${props.addressAt(row, col)}`)
}

/** 改过的格子悬停时那一句：谁改的、原来是什么。 */
function changedTitle(row: number, col: number): string | null {
  const before = beforeAt(row, col)
  if (before === undefined) return null
  return before
    ? t('work.room.versions.cellChanged', { who: props.changedBy, before: props.cellText(before) })
    : t('work.room.versions.cellChangedFromEmpty', { who: props.changedBy })
}
</script>

<template>
  <ol class="ps-cards">
    <template v-for="(row, r) in rows" :key="r">
      <li v-if="r > 0" class="ps-card">
        <button
          type="button"
          class="ps-card__title"
          :class="{ 'is-on': selected === addressAt(r, 0), 'is-chg': changedTitle(r, 0) !== null }"
          :title="changedTitle(r, 0) ?? undefined"
          @click="emit('choose', r, 0)"
        >
          <span class="ps-card__name"
            ><del v-if="showBefore && beforeAt(r, 0)" class="ps-card__was">{{ cellText(beforeAt(r, 0) ?? '') }}</del
            >{{ cellText(row[0] ?? '') || label(0) }}</span
          >
          <span class="ps-card__row t-meta">{{ t('work.room.preview.sheetRow', { row: r + 1 }) }}</span>
        </button>
        <dl class="ps-card__fields">
          <div
            v-for="c in fields(row, r)"
            :key="c"
            class="ps-card__field"
            :class="{ 'is-on': selected === addressAt(r, c), 'is-chg': changedTitle(r, c) !== null }"
            :title="changedTitle(r, c) ?? undefined"
          >
            <dt class="ps-card__label">{{ label(c) }}</dt>
            <dd class="ps-card__value">
              <button type="button" class="ps-card__cell" @click="emit('choose', r, c)">
                <del v-if="showBefore && beforeAt(r, c)" class="ps-card__was">{{ cellText(beforeAt(r, c) ?? '') }}</del>
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

/* 这一版改过的那一格：和表格里同一种标法，琥珀浅底加一圈细边。选中的描边排在后面，
   两种同时成立时选中更显眼。 */
.ps-card__title.is-chg,
.ps-card__field.is-chg {
  background: var(--accent-wash);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 55%, var(--surface));
}
.ps-card__was {
  margin-right: 6px;
  color: var(--danger-ink);
  text-decoration: line-through;
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
