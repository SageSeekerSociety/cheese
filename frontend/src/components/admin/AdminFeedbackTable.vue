<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import FeedbackStatusChip from '@/components/feedback/FeedbackStatusChip.vue'
import { KIND_LABEL, priorityMeta, SOURCE_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

/**
 * AdminFeedbackTable.vue — 11 列总表（§5.1）。
 *
 * **没有复用 `AdminGrid.vue`，是故意另写一张。** 读完之后它不是这张表的 90%：两者共
 * 用的只有「sticky 表头 + colgroup + 39px 行」这三样，而这张表要的东西恰好都在通用壳
 * 的对立面 —— 选中行要一条 2px 琥珀竖条（`AdminGrid` 的 `<td>` 内边距是 8/12，想压过
 * 它得凑三到四个类，它自己的注释里就写了这条）、空态是一个 320px 宽的主副两行块而不是
 * 一句居中灰字（那条规则的选择器是 `(0,3,1)`，几乎盖不动）、骨架要按 11 列各自的百分比
 * 长骨头。三处都要靠提权重的覆盖去打架，而 `AdminGrid` 又是 §10.3 点名「不动」的文件。
 * 反过来，复用它的代价是把两个页面的几何绑在一起：成员表那一格的 32px 按钮会把行撑到
 * 48，而这张表的 39px 是量出来的、不能被别处的内容牵动。
 *
 * 列宽只有一份（`<colgroup>` + `table-layout: fixed`）：表头和表体各写一遍的话，两边
 * 迟早差一档，表现是表头文字和它下面那一列对不上。宽度写死的 10 列合计 916，第 2 列
 * 不写宽度 = 吃掉剩下的全部（1100 − 916 = 184）。
 *
 * 不虚拟滚动，同队列（§13 C-05）。
 */

const props = defineProps<{
  items: FeedbackCard[]
  /** 选中行。`null` = 没有选中 —— 表格视图里选中行是唯一一处 amber（§7.4），
   *  没有选中就不该有那一条。 */
  activeId: string | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'open', id: string): void
  (e: 'activate', id: string): void
}>()

const { t } = useI18n()

/** 11 列，顺序即渲染顺序。 */
const COLUMNS = [
  'id',
  'title',
  'user',
  'source',
  'kind',
  'priority',
  'status',
  'supports',
  'comments',
  'assignee',
  'updated',
] as const

type ColKey = (typeof COLUMNS)[number]

/** 列头文案的键，**逐条写成字面量**，不拼 `` `feedback.table.col.${col}` ``：
 *  `i18n/catalog.spec.ts` 是在源码里按字面量找引用的，拼出来的键它看不见 —— 那 11 条
 *  会同时被判成「没有任何文件引用」和「没有英文」，两个测试一起红。这里存键、模板里
 *  `t(COL_LABEL[col])`，引用的字面量在源码里，翻译仍然是响应式的（不是在 setup 里
 *  先把 `t()` 调完存字符串）。 */
const COL_LABEL: Record<ColKey, string> = {
  id: 'feedback.table.col.id',
  title: 'feedback.table.col.title',
  user: 'feedback.table.col.user',
  source: 'feedback.table.col.source',
  kind: 'feedback.table.col.kind',
  priority: 'feedback.table.col.priority',
  status: 'feedback.table.col.status',
  supports: 'feedback.table.col.supports',
  comments: 'feedback.table.col.comments',
  assignee: 'feedback.table.col.assignee',
  updated: 'feedback.table.col.updated',
}

/** 同上表：写死的是 10 列，`null` 那一列（标题）吃剩下的。 */
const COL_WIDTHS: (string | null)[] = [
  '76px', // 1 ID
  null, // 2 标题
  '184px', // 3 用户
  '92px', // 4 来源
  '56px', // 5 类型
  '72px', // 6 优先级
  '136px', // 7 状态
  '48px', // 8 支持
  '48px', // 9 评论
  '116px', // 10 指派
  '88px', // 11 更新
]

/** 右对齐的三列：两个计数和一个时间。其余各列左对齐。 */
const RIGHT_ALIGNED = new Set(['supports', 'comments', 'updated'])

/** 骨条占各自格宽的比例（§5.1 的 11 个值）。长 - 中 - 短 - 小 走一遍：每行的骨头都
 *  一样长的话，骨架看着像一条条对齐的横线，比空白更晃眼。 */
const BONE_WIDTHS = [70, 86, 72, 58, 44, 64, 78, 40, 40, 60, 56]

/** 骨架行数 = floor(表体可视高 / 39) − 2；1440×900 基准下是 18 行（§5.1）。 */
const SKELETON_ROWS = 18

const tableEl = ref<HTMLElement | null>(null)

/** 键盘的停靠行：**没有选中行时落在第一行，但不画选中条** —— 「光标停在这」和
 *  「这一条被选中」是两件事。合成一个的话，表格刚打开就有一条琥珀指着一条根本没被选中
 *  的反馈；而不给停靠行的话，一个停靠点都没有，Tab 进不来这张表，`j` 也就没有起点。 */
const cursorId = computed(() => props.activeId ?? props.items[0]?.id ?? null)

const activeRowId = computed(() => (cursorId.value ? `ftrow-${cursorId.value}` : undefined))

/** 同队列：链接按类名找，不给每一行加一个契约里没有的 ref。 */
function focusRow(i: number) {
  const links = tableEl.value?.querySelectorAll<HTMLElement>('.fbrow__link')
  links?.item(i)?.focus()
}

function move(delta: number) {
  const from = props.items.findIndex((item) => item.id === cursorId.value)
  const to = from + delta
  if (from < 0 || to < 0 || to >= props.items.length) return
  emit('activate', props.items[to].id)
  // 焦点跟着光标走，等新值传下来、行重新渲染之后再聚焦。
  void nextTick(() => focusRow(to))
}

function onKeydown(e: KeyboardEvent) {
  switch (e.key) {
    case 'j':
    case 'ArrowDown':
      e.preventDefault()
      move(1)
      break
    case 'k':
    case 'ArrowUp':
      e.preventDefault()
      move(-1)
      break
    case 'Enter':
      if (!cursorId.value) return
      e.preventDefault()
      emit('open', cursorId.value)
      break
  }
}

const when = (item: FeedbackCard) => relTime(item.last_activity_at ?? item.created_at)

/** 优先级那一格是一行 13px 的 `--muted` 字（§5.1 第 6 列），**不带圆点也不铺底色**：
 *  一屏十几行，两列都上色的话整张表先变成一条条色块，wash 留给一屏只有一个的地方
 *  （抽屉、卡片）。无值显 `—`：类型上 `priority` 不可空，但这一格不该因为上游多塞一个
 *  空值就渲染出「未知」两个字。 */
const priLabel = (item: FeedbackCard) => (item.priority ? priorityMeta(item.priority).label : '—')
</script>

<template>
  <div class="aft">
    <div class="aft__scroll">
      <table
        ref="tableEl"
        class="aft__table"
        role="grid"
        :aria-label="t('feedback.table.label')"
        :aria-busy="loading"
        :aria-activedescendant="activeRowId"
        data-owns-arrow-keys
        @keydown="onKeydown"
      >
        <colgroup>
          <col v-for="(width, i) in COL_WIDTHS" :key="i" :style="width ? { width } : undefined" />
        </colgroup>

        <thead class="aft__head">
          <tr role="row">
            <th
              v-for="col in COLUMNS"
              :key="col"
              scope="col"
              role="columnheader"
              :class="{ aft__num: RIGHT_ALIGNED.has(col) }"
            >
              {{ t(COL_LABEL[col]) }}
            </th>
          </tr>
        </thead>

        <tbody class="aft__body">
          <template v-if="loading">
            <!-- 骨架走同一张表、同一份 colgroup、同一个 39px：数据到货那一刻整张表不重排，
                 而骨架存在的全部意义就是这个。 -->
            <tr v-for="i in SKELETON_ROWS" :key="`skel-${i}`" class="aft__row" role="row" aria-hidden="true">
              <!-- 骨条宽度就是列的循环变量：两个数组一一对应，少写一个下标。 -->
              <td v-for="(bone, c) in BONE_WIDTHS" :key="c" role="gridcell">
                <span class="aft__bone" :style="{ width: `${bone}%` }" />
              </td>
            </tr>
          </template>

          <tr v-else-if="!items.length" class="aft__row" role="row">
            <td class="aft__none-cell" role="gridcell" :colspan="COLUMNS.length">
              <div class="aft__none">
                <!-- 空态留在卡里：表头不画掉，否则「这是哪张表的空」就只剩一个孤零零的框。
                     筛选无结果与加载失败是页面知道、这张表不知道的两件事，所以留一个出口。 -->
                <slot name="empty">
                  <p class="aft__none-title">{{ t('feedback.table.empty.title') }}</p>
                  <p class="aft__none-desc">{{ t('feedback.table.empty.desc') }}</p>
                </slot>
              </div>
            </td>
          </tr>

          <template v-else>
            <tr
              v-for="item in items"
              :id="`ftrow-${item.id}`"
              :key="item.id"
              class="fb-row aft__row"
              :class="{ 'aft__row--active': item.id === activeId }"
              role="row"
              :aria-selected="item.id === activeId"
            >
              <td class="aft__cell aft__mono t-meta-read t-num">{{ item.display_id }}</td>

              <!-- 整行可点，但只占一个 Tab 停靠点：链接是一条真的 `<button>`，它的
                   `::after` 铺满整行。停靠点跟着光标行走（roving tabindex），所以只有
                   光标行的链接是 0，其余是 -1 —— Tab 进这张表停一次，不是停十一列。
                   两处代价，和上一代同一张表里那一段说的是同一件事：① 别的格里没法用
                   鼠标划选文字（那一层盖在上面）；② 将来某一行要加自己的按钮时，那个
                   按钮得写 `position: relative; z-index` 才点得到（队列行那颗就是这么
                   写的）。 -->
              <td class="aft__cell aft__title">
                <button
                  type="button"
                  class="fbrow__link"
                  :tabindex="item.id === cursorId ? 0 : -1"
                  @click="emit('activate', item.id)"
                >
                  {{ item.title }}
                </button>
              </td>

              <td class="aft__cell">{{ item.author_handle }}</td>

              <td class="aft__cell">{{ SOURCE_LABEL[item.author_is_agent ? 'agent' : 'user'] }}</td>

              <td class="aft__cell">{{ KIND_LABEL[item.kind] }}</td>

              <td class="aft__cell">{{ priLabel(item) }}</td>

              <td class="aft__cell aft__cell--status">
                <FeedbackStatusChip :status="item.status" />
              </td>

              <td class="aft__cell aft__num aft__mono t-meta-read t-num">{{ item.supports }}</td>

              <td class="aft__cell aft__num aft__mono t-meta-read t-num">{{ item.comments }}</td>

              <td class="aft__cell">
                <template v-if="item.assignee_handle">{{ item.assignee_handle }}</template>
                <span v-else>—</span>
              </td>

              <td class="aft__cell aft__num t-meta-read t-num">{{ when(item) }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 这张卡**没有** `overflow`：加了就是一个新的滚动/裁剪上下文，内层那个 sticky 表头会
   改挂到它身上。裁剪交给内层那个滚动容器。 */
.aft {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.aft__scroll {
  flex: 1 1 auto;
  min-height: 0;
  /* 纵向：表头 sticky 的参照物必须是这一层。横向：窄屏时整张表横着滚，而不是把 11 列
     挤到读不出来。 */
  overflow: auto;
}

.aft__table {
  width: 100%;
  /* 1100 是这一页的内容列宽，减掉卡片左右各 1px 描边还剩 1098 —— 表体按 100% 走，
     这一条只是窄屏下的下限。写成 1100 的话，1100px 的列宽下会常驻一条 2px 的横向滚动条。 */
  min-width: 1080px;
  table-layout: fixed;
  border-collapse: separate;
  border-spacing: 0;
}

.aft__head th {
  position: sticky;
  top: 0;
  z-index: 1;
  box-sizing: border-box;
  height: 36px;
  padding: 0 12px;
  /* sticky 时**不能透明**，否则行会从表头文字底下穿过去。 */
  background: var(--surface);
  border-bottom: 1px solid var(--line-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  text-align: left;
  white-space: nowrap;
}

.aft__head th:first-child {
  padding-left: 16px;
}

.aft__head th:last-child {
  padding-right: 16px;
}

.aft__row {
  position: relative;
  /* 行高钉死：骨架行里只有一根 12px 的骨头，真行里有芯片和按钮，不钉的话数据到货那一刻
     每一行都往下长几像素，整屏跳一次。 */
  height: 39px;
}

.aft__cell {
  overflow: hidden;
  padding: 0 12px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: left;
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
}

.aft__body > tr > .aft__cell:first-child {
  padding-left: 16px;
}

.aft__body > tr > .aft__cell:last-child {
  padding-right: 16px;
}

/* 表头那一条线属于表头；最后一行不画线 —— 画了会和卡片自己的描边挤成两条。 */
.aft__body > tr:last-child > .aft__cell {
  border-bottom: 0;
}

/* 首末行的圆角得自己画：卡片没有 `overflow`（见上），行的底色是方的，悬停 / 选中时会
   盖住卡片描边的那道弧。四条都是 longhand —— stylelint 按字面比较圆角值，简写会被判成
   新违规。 */
.aft__head > tr:first-child > th:first-child {
  border-top-left-radius: var(--radius-lg);
}

.aft__head > tr:first-child > th:last-child {
  border-top-right-radius: var(--radius-lg);
}

.aft__body > tr:last-child > .aft__cell:first-child {
  border-bottom-left-radius: var(--radius-lg);
}

.aft__body > tr:last-child > .aft__cell:last-child {
  border-bottom-right-radius: var(--radius-lg);
}

/* 标题列是正文，不是次级字段：`--text` 一档、14px（§5.1 的第 2 列）。 */
.aft__title {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}

.fbrow__link {
  display: block;
  max-width: 100%;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.fbrow__link::after {
  content: '';
  position: absolute;
  inset: 0;
}

/* ID / 支持 / 评论用等宽：这几列的值全是 ASCII，等宽让同一列的数字右边缘对齐。
   「更新」那一列**不写** —— 它是 `relTime` 算出来的相对时间（「刚刚」「昨天」「3天前」），
   而 mono 栈是 JetBrains Mono，没有 CJK 字形。 */
.aft__mono {
  font-family: var(--font-mono);
}

.aft__num {
  text-align: right;
}

/* §5.1 给这一格的芯片是 20px 高，组件本体是 18.8px。那个文件不能按这一张表去改：它和
   `.chip-neutral` 在详情页顶部并排，两处必须同高。这里只在表格的语境里给它 20px，
   影响不到别处。 */
.aft__cell--status :deep(.fb-chip) {
  box-sizing: border-box;
  height: 20px;
}

/* 选中行：底色 + 左侧 2px 琥珀竖条，全表唯一一处 amber（§7.4）。竖条用 inset box-shadow
   而不是 `border-left` —— 加边框会把这一行整列的字右推 2px，选中 / 取消选中时字在跳。
   `background-color` 而不是 `background`，并且把 hover 那一档一起压住：`.fb-row:hover`
   的 `--fill` 比 `--fill-2` 更**浅**，不压的话指针一放上去，选中行看起来像被取消选中。 */
.aft__row.aft__row--active,
.aft__row.aft__row--active:hover {
  background-color: var(--fill-2);
}

.aft__row--active > .aft__cell:first-child {
  box-shadow: inset 2px 0 0 var(--accent);
}

.aft__bone {
  display: block;
  height: 12px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

/* 空态 / 错误态占满整个表体：顶部对齐、距表头下沿 96px、宽 320px 居中（§5.1）。 */
.aft__none-cell {
  padding: 96px 12px 0;
  vertical-align: top;
}

.aft__none {
  width: 320px;
  margin: 0 auto;
}

.aft__none-title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.aft__none-desc {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
