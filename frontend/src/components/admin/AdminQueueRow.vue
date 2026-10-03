<script setup lang="ts">
import type { FeedbackCard, FeedbackStatus } from '@/cx_types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import StatusRail from '@/components/admin/StatusRail.vue'
import { priorityMeta, statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'

/**
 * AdminQueueRow.vue — 队列里的一行（61px，§5.2）。
 *
 * **这一行有两个动作，它们是两件事，所以是两个事件。** `activate` 只是「光标落在
 * 这一行」—— 选中不写任何东西，j/k、指针点标题、点空白都算它。`advance` 是「把这一条
 * 推到下一格」，它会落到服务端。合成一个事件的话，页面就没法只处理「移过去看看」：
 * 每按一次 j 都会推一次状态，而这是不可撤销的写入（撤销条只有一层、5 秒）。
 *
 * 61px = 9 + 20（标题行盒）+ 4 + 18（meta 行盒）+ 9 + 1（`--line` 分隔线）。四个数
 * 都是钉死的：标题和 meta 的行盒由各自的字号决定（14 / 12），换字号就得重新量这一行，
 * 而不是让行高跟着内容长。
 *
 * 右侧三列（指派 116 / 更新 88 / 下一步 104）宽度都是固定的，只有标题那一列吃剩下
 * 的空间（§4.1 的算式里它是 F）。按钮本身仍是 `max-content`，但它住在一格定宽、靠右
 * 的格子里：按钮一宽一窄不再带动左边的指派和更新整体平移，列头也就能逐列对齐。104 是
 * 最长的文案（英文 “Mark shipped”）加内边距；已上线没有按钮，格子照样占着位置。
 *
 * 行的 `id` 直接由 `item.id` 长出来：列表容器要用 `aria-activedescendant` 指到当前
 * 行，而这个 prop 是契约里没有的，两边只能靠同一个名字约定，不能靠传值。
 */

const props = defineProps<{
  item: FeedbackCard
  /** 光标是不是停在这一行。它同时决定 Tab 停靠点（roving tabindex 的「roving」）。 */
  active: boolean
  /** 混合状态的列表里要写状态字；单状态的页签下它是一列重复四遍的字，省掉。 */
  showStatusWord: boolean
}>()

const emit = defineEmits<{
  (e: 'activate'): void
  (e: 'advance'): void
}>()

const { t } = useI18n()

/** 下一格是谁。`deployed` 是梯子的最后一格，没有下一格。 */
const NEXT: Record<FeedbackStatus, FeedbackStatus | null> = {
  received: 'in_progress',
  in_progress: 'resolved',
  resolved: 'deployed',
  deployed: null,
  declined: null,
}

/** 按钮文案。按**目标**状态取，不按当前状态：按钮说出口的是它要做的那件事。 */
const ADVANCE_LABEL: Record<FeedbackStatus, string> = {
  in_progress: 'feedback.queue.advance.inProgress',
  resolved: 'feedback.queue.advance.resolved',
  deployed: 'feedback.queue.advance.deployed',
  received: '',
  declined: '',
}

const rowId = computed(() => `fbrow-${props.item.id}`)

const status = computed(() => statusMeta(props.item.status))

/** 有下一格才画按钮。没画不是「禁用」—— 已上线就是到头了，一个灰掉的按钮会让人
 *  以为「还差个条件没满足」。 */
const next = computed(() => NEXT[props.item.status] ?? null)

const advanceLabel = computed(() => (next.value ? t(ADVANCE_LABEL[next.value]) : ''))

/** meta 行里的时间是**提交时间**：它跟的是提交人，答的是「谁什么时候提的」。 */
const createdAt = computed(() => relTime(props.item.created_at))

/** 右侧「更新」列是**最后活动**时间，答的是另一件事：「这条还动不动」。
 *  队列里真正决定先看哪一条的是后者，所以两处不合并成一个字段。 */
const updatedAt = computed(() => relTime(props.item.last_activity_at ?? props.item.created_at))
</script>

<template>
  <div
    :id="rowId"
    class="fb-row qrow"
    :class="{ 'qrow--active': active }"
    role="row"
    :aria-selected="active"
    :data-status="item.status"
  >
    <StatusRail :status="item.status" class="qrow__rail" />

    <span class="qrow__main" role="gridcell">
      <!-- 整行可点，但只占一个 Tab 停靠点：链接是一条真的 `<button>`，它的 `::after`
           铺满整行，于是点在行里任何位置都等于点它，而 Tab 只在这里停一次。
           行上挂 `@click` 的老写法鼠标能用、键盘整段跳过去，那才是规范禁止的。 -->
      <button type="button" class="fbrow__link" :tabindex="active ? 0 : -1" data-user-content @click="emit('activate')">
        {{ item.title }}
      </button>

      <span class="qrow__meta t-num">
        <!-- 优先级字只画「高 / 紧急」两档：分诊排序的第一信号。低 / 普通不画 ——
             满屏「普通」是噪音，缺省即普通。颜色走语义类而不是 `:style` 绑
             `priorityMeta().ink`：颜色字面量要留在 stylelint 看得见的 CSS 里。 -->
        <template v-if="item.priority === 'high' || item.priority === 'urgent'">
          <span class="qrow__pri" :class="item.priority === 'urgent' ? 'qrow__pri--urgent' : 'qrow__pri--high'">
            {{ priorityMeta(item.priority).label }}
          </span>
          <span class="qrow__sep" aria-hidden="true">·</span>
        </template>
        <template v-if="showStatusWord">
          <span class="qrow__word">{{ status.label }}</span>
          <span class="qrow__sep" aria-hidden="true">·</span>
        </template>
        <span>{{ item.display_id }}</span>
        <span class="qrow__sep" aria-hidden="true">·</span>
        <span>{{ item.author_handle }}</span>
        <span class="qrow__sep" aria-hidden="true">·</span>
        <span>{{ createdAt }}</span>
        <span class="qrow__sep" aria-hidden="true">·</span>
        <span>{{ t('feedback.queue.supports', { n: item.supports }) }}</span>
        <span class="qrow__sep" aria-hidden="true">·</span>
        <span>{{ t('feedback.queue.comments', { n: item.comments }) }}</span>
      </span>
    </span>

    <span class="qrow__assignee" role="gridcell">
      <!-- 窄屏下这一格不再是「列头底下的一列」，它单独占一行，前面得有这句话才读得出
           「carol」是干什么的。宽屏那一列有列头（`qrow__updated` 同理靠右对齐的列位
           说明自己），所以标签只在窄屏画出来。 -->
      <span class="qrow__alabel">{{ t('feedback.queue.col.assignee') }}</span>
      <!-- 值单独包一层：窄屏那个标签也是这一格的文字，读「指派给了谁」的地方（e2e 的
           feedback-flows 就是一处）要读的是这一层，不是整格。 -->
      <span v-if="item.assignee_handle" class="qrow__avalue">{{ item.assignee_handle }}</span>
      <span v-else class="qrow__avalue qrow__dim">—</span>
    </span>

    <span class="qrow__updated t-meta-read t-num" role="gridcell">{{ updatedAt }}</span>

    <span class="qrow__action" role="gridcell">
      <button v-if="next" type="button" class="qrow__btn" @click="emit('advance')">{{ advanceLabel }}</button>
    </span>
  </div>
</template>

<style scoped>
.qrow {
  display: flex;
  /* 行高 61px 是铁的（§5.2，验收时会逐行量）。这一行同时是 `.qlist` 这个纵向 flex
     容器的**项目**，而 flex 项目的默认 `flex-shrink` 是 1 —— 50 行塞进一屏时，它们
     会先被压扁再去溢出滚动，61 会变成个位数。`flex: 0 0 auto` 把它钉住。 */
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  /* 整行 = 一个可聚焦的链接，那个 `::after` 要有一个定位基准。 */
  position: relative;
  height: 61px;
  padding: 9px 20px 9px 16px;
  border-bottom: 1px solid var(--line);
}

/* 最后一行不画线：它下面是卡片的描边，两条线叠在一起会变粗。行高是 border-box 里的
   定值，去掉这一条不会让最后一行矮 1px。 */
.qrow:last-child {
  border-bottom: 0;
}

/* 光标行只换底色（队列里 amber = 0 处，§7.4）：配上左侧那根阶梯条和落在这一行上的
   焦点环，位置说得很清楚。`background-color` 而不是 `background`，并且把 hover 那一档
   一起写进来 —— `.fb-row:hover` 的 `--fill` 比 `--fill-2` 更**浅**，不压住的话指针一放
   上去，选中行看起来像是被取消了选中。 */
.qrow.qrow--active,
.qrow.qrow--active:hover {
  background-color: var(--fill-2);
}

.qrow__rail {
  flex: 0 0 auto;
}

/* 缝 12px：阶梯条和标题之间比其余各列窄，因为那 4px 的条只是行的边，不是一列数据。
   `min-width: 740px` 是 §4.1 算式里的 F 的最小值 —— 窄屏下这一列不再被压，整行让
   容器横着滚（`.qlist` 是 `overflow: auto`），和列头写的是同一个下限。 */
.qrow__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 4px;
  min-width: 740px;
  margin: 0 16px 0 12px;
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

/* meta 行。12px 而不是 12.5：它是行的第二行、比标题低一档，规格给的是 12（§5.2）。
   行高 18 = `--lh-12`，这一行的高度是 61px 算式里的一项。 */
.qrow__meta {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.qrow__word {
  flex: 0 0 auto;
}

/* 优先级字：meta 行首，只有高 / 紧急两档会画出来（理由在模板那段注释里）。
   新项都在 meta 行首 / 尾：窄屏 `overflow: hidden` 截断时先切新项，61px 算式不动。 */
.qrow__pri {
  flex: 0 0 auto;
  font-weight: 600;
}

.qrow__pri--high {
  color: var(--warn-ink);
}

.qrow__pri--urgent {
  color: var(--danger-ink);
}

.qrow__sep {
  flex: 0 0 auto;
  color: var(--muted);
}

.qrow__assignee {
  flex: 0 0 116px;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qrow__dim {
  color: var(--muted);
}

/* 指派那一格的行内标签。宽屏没有它（列头写着「指派」），窄屏才有 —— 规则在下面那条
   媒体查询里。 */
.qrow__alabel {
  display: none;
}

/* 更新列不写 `--font-mono`，即使 §5.1 的那一列标着 mono：这个值是 `relTime` 算出来的
   相对时间（「刚刚」「昨天」「3天前」），而 mono 栈是 JetBrains Mono，**没有 CJK 字形**
   —— 汉字会回落到系统字体、数字留在等宽里，同一格里两种字体。等宽数字由 `.t-num`
   给（`.t-meta-read` 带 12.5 / `--lh-12` / `--muted`）。 */
.qrow__updated {
  flex: 0 0 88px;
  overflow: hidden;
  margin-left: 16px;
  color: var(--muted);
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qrow__action {
  display: flex;
  flex: 0 0 104px;
  justify-content: flex-end;
  margin-left: 16px;
}

/* 24px 高、宽 max-content、最小 56px（§5.2）。中性色而不是 amber：队列里一处橙色都
   没有（§7.4），每一次点击都染一个橙色的话，这一列橙色也就不再指任何东西了。
   `position: relative` + `z-index` 是为了从 `.fbrow__link::after` 那一层上面露出来，
   否则按钮点不到（点到的是整行链接）。 */
.qrow__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  position: relative;
  z-index: 1;
  width: max-content;
  min-width: 56px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition: background-color 0.12s ease;
}

@media (hover: hover) and (pointer: fine) {
  .qrow__btn:hover {
    background: var(--fill);
  }
}

/* 窄屏（≤700，和 `AdminPage` 同一条线）：**一行翻成一张小卡片**，
   不再横着滚。原来那条「整行 1100px、容器横着滚」在 390 下等于把「指派」整列和
   「下一步」按钮推到屏幕外 —— 而这两样正是这一页要回答的问题（归谁、我该做什么），
   看不见就等于这条队列只剩标题可读。61px 的行高在这一档放开：卡片按内容长。 */
@media (max-width: 700px) {
  .qrow {
    flex-wrap: wrap;
    align-items: center;
    height: auto;
    min-height: 61px;
    padding: 12px 16px 12px 24px;
    row-gap: 6px;
    column-gap: 12px;
  }

  /* 阶梯条退出 flex 流、贴在卡片左沿：标题那格 basis 100% 之后，条若还是一个 flex
     项目，就会自己占掉第一行。 */
  .qrow__rail {
    position: absolute;
    top: 12px;
    left: 10px;
  }

  /* 标题和 meta 独占第一行（basis 100%），右边三格整组掉到第二行成为卡片的底栏。
     给个「够大的下限」不够：390 下 240 + 指派那格还挤得下一行，「指派 —」就浮在
     标题右边。`min-width: 0` 保住长标题的截断。 */
  .qrow__main {
    flex: 1 1 100%;
    min-width: 0;
    margin: 0;
  }

  /* 卡片里高度是自由的，标题折两行比一句省略号有用。 */
  .fbrow__link {
    white-space: normal;
  }

  .qrow__meta {
    flex-wrap: wrap;
    row-gap: 2px;
    white-space: normal;
  }

  /* 第二行：指派靠左，更新与按钮靠右（grow 给指派，它吃中间那段空）。 */
  .qrow__assignee {
    flex: 1 1 auto;
    min-width: 0;
  }

  .qrow__alabel {
    display: inline;
    margin-right: 6px;
    color: var(--muted);
  }

  .qrow__updated {
    flex: 0 0 auto;
    margin-left: 0;
    text-align: left;
  }

  .qrow__action {
    flex: 0 0 auto;
    margin-left: 0;
  }

  .qrow__action:empty {
    display: none;
  }
}
</style>
