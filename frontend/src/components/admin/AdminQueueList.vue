<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminQueueRow from '@/components/admin/AdminQueueRow.vue'

/**
 * AdminQueueList.vue — 队列本体：一张卡（28px 列头 + 61px 行 + 可选列表脚）。
 *
 * **焦点只有一个停靠点，它跟着「当前行」走。** 行里那条链接的 `tabindex` 由 `active`
 * 决定（当前行 0、其余 -1），这就是 roving tabindex：Tab 进队列落在当前行上，不是落在
 * 容器上、也不是落在每一行上。焦点画在整行（`style.css` 的 `.fb-row:has(.fbrow__link:
 * focus-visible)`），所以容器上那份 `aria-activedescendant` 是**第二重**声明 —— 读屏
 * 落在容器上时也能问出「当前行是哪一条」，而焦点真的在链接里时它会被忽略。两份都要
 * 写，因为它们各自对不同的读屏路径负责。
 *
 * 容器的 `keydown` 只认 `j` / `k` / `↑` / `↓` / `Enter`（§8 的「列表」作用域）。**分诊键
 * （1 / 2 / 3 / H / U / A / M）不在这里**：它们要落到 store，而这一层只拿到 `items`，
 * 拿不到写入口；由队列页在拿到事件后统一分发。`j`/`k` 只挪 `activeIndex`，**不发详情
 * 请求** —— 请求由页面去防抖（F-06），连按十次 `j` 只该产生一次请求。
 *
 * 队列不做虚拟滚动（§13 C-05）：50 行 DOM 换掉的是 `aria-activedescendant` 与 roving
 * tabindex 的双份实现复杂度，而且虚拟列表会把 `.fb-row:has(...)` 那一套整行焦点环
 * 拆掉。所以这里是老老实实的 `v-for`。
 */

const props = defineProps<{
  items: FeedbackCard[]
  activeIndex: number
  loading: boolean
  showStatusWord: boolean
}>()

const emit = defineEmits<{
  (e: 'update:activeIndex', v: number): void
  (e: 'advance', id: string): void
  (e: 'open', id: string): void
}>()

const { t } = useI18n()

/** 骨架 11 行（§9.1）：61px × 11 = 671px。行数和行高都取自真实行，数据到货那一刻
 *  这一页不重排 —— 骨架存在的全部意义就是这个。 */
const SKELETON_ROWS = 11

const gridEl = ref<HTMLElement | null>(null)

/** 当前行。**越界时回落到第一行**，而不是「没有当前行」：没有当前行就等于没有任何一行
 *  的链接 `tabindex` 是 0，整条队列 Tab 不进来，`j` 也就永远没有起点 —— 键盘用户会
 *  停在这一页外面，而页面上没有任何东西告诉他为什么。 */
const cursor = computed(() => {
  if (!props.items.length) return -1
  return Math.min(Math.max(props.activeIndex, 0), props.items.length - 1)
})

/** `<AdminQueueRow>` 的行 id 由 `item.id` 拼出来，这里照同一个约定拼，**不靠传值**：
 *  行的 props 是契约钉死的，没有 id 这一项。 */
const activeRowId = computed(() => (cursor.value >= 0 ? `fbrow-${props.items[cursor.value].id}` : undefined))

/** 链接元素直接按类名找，不问每一行要 ref：行是子组件，逐个透传 ref 只会多一层
 *  契约里没有的约定，而 `.fbrow__link` 这个名字本来就是两边共用的（style.css 的整行
 *  焦点环也按它找）。 */
function focusRow(i: number) {
  const links = gridEl.value?.querySelectorAll<HTMLElement>('.fbrow__link')
  links?.item(i)?.focus()
}

function move(delta: number) {
  const to = cursor.value + delta
  if (to < 0 || to >= props.items.length || to === cursor.value) return
  emit('update:activeIndex', to)
  // roving tabindex：焦点得跟着新行走。等父级把新值传下来、行重新渲染之后再聚焦，
  // 否则聚焦的是**旧顺序**里的第 to 个链接。
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
      if (!activeRowId.value || cursor.value < 0) return
      e.preventDefault()
      emit('open', props.items[cursor.value].id)
      break
  }
}
</script>

<template>
  <div class="qlist">
    <div
      ref="gridEl"
      class="qlist__grid"
      role="grid"
      :aria-label="t('feedback.queue.label')"
      :aria-busy="loading"
      :aria-activedescendant="activeRowId"
      data-owns-arrow-keys
      @keydown="onKeydown"
    >
      <!-- 列头。右侧那三格跟着行的同一套宽度走，但**不逐列对齐**：按钮是
           `max-content`（§5.2），它一宽一窄会带动它左边的三列整体平移，所以任何固定
           的列头宽度都只能对上其中一部分行。这几格落在自己那一列的范围里，读的时候
           够用了 —— 想把它们钉死，得先把按钮改成定宽，那是改规格不是改这里。 -->
      <div class="qlist__head" role="row">
        <span class="qlist__head-title" role="columnheader">{{ t('feedback.queue.col.title') }}</span>
        <span class="qlist__head-assignee" role="columnheader">{{ t('feedback.queue.col.assignee') }}</span>
        <span class="qlist__head-updated" role="columnheader">{{ t('feedback.queue.col.updated') }}</span>
        <span class="qlist__head-next" role="columnheader">{{ t('feedback.queue.col.next') }}</span>
      </div>

      <template v-if="loading">
        <!-- 骨架行也是真的 `role="row"`，走同一套高度和内边距：换成一个独立的 div 形态
             的话，数据到货那一刻整条队列会重排一次。 -->
        <div v-for="i in SKELETON_ROWS" :key="`skel-${i}`" class="qskel" role="row" aria-hidden="true">
          <span class="qskel__rail" role="gridcell" />
          <span class="qskel__main" role="gridcell">
            <span class="qskel__bone qskel__bone--title" />
            <span class="qskel__bone qskel__bone--meta" />
          </span>
          <span class="qskel__assignee" role="gridcell">
            <span class="qskel__bone qskel__bone--assignee" />
          </span>
          <span class="qskel__updated" role="gridcell">
            <span class="qskel__bone qskel__bone--updated" />
          </span>
          <span class="qskel__action" role="gridcell">
            <span class="qskel__bone qskel__bone--action" />
          </span>
        </div>
      </template>

      <div v-else-if="!items.length" class="qlist__none" role="row">
        <div class="qlist__none-box" role="gridcell">
          <!-- 空态留在卡里（表头不画掉，否则「这是哪张队列的空」就只剩一个孤零零的框）。
               筛选无结果与加载失败是页面知道、这一层不知道的两件事，所以留一个出口。 -->
          <slot name="empty">
            <p class="qlist__none-title">{{ t('feedback.queue.empty.title') }}</p>
            <p class="qlist__none-desc">{{ t('feedback.queue.empty.desc') }}</p>
          </slot>
        </div>
      </div>

      <template v-else>
        <AdminQueueRow
          v-for="(item, i) in items"
          :key="item.id"
          :item="item"
          :active="i === cursor"
          :show-status-word="showStatusWord"
          @activate="emit('update:activeIndex', i)"
          @advance="emit('advance', item.id)"
        />
      </template>
    </div>

    <!-- 列表脚（§4.1 的 40px「13 行 · 已到底」）。几何在这一层（分隔线 + 高度），
         文字留给页面：它才拿得到「还有没有下一页」。 -->
    <div v-if="$slots.foot" class="qlist__foot">
      <slot name="foot" />
    </div>
  </div>
</template>

<style scoped>
/* 高度契约和 `AdminGrid.vue` 的 `.agrid` 一样：父级把它放进一个定高的纵向 flex 列
   里，它自己吃剩下的那一段、在里面滚。父级不定高时这两条不起作用，卡片就按内容长。 */
.qlist {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  /* `overflow: auto` 而不是 `hidden`：窄屏（< 约 1100）时整条队列横着滚，而不是把
     右边的按钮裁掉。顺带把行 hover 的底色裁在圆角里。队列没有 sticky 表头，所以
     不像 `AdminGrid` 那样必须避开 `overflow`。 */
  overflow: auto;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.qlist__head {
  display: flex;
  /* 和行同理：列头 28px 也不能被挤（见 `.qrow` 那段）。 */
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  /* 左 32 = 行的左内边距 16 + 阶梯条 4 + 缝 12：列头第一格和标题的左边缘对齐。 */
  height: 28px;
  padding: 0 20px 0 32px;
  border-bottom: 1px solid var(--line-2);
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
}

.qlist__head-title {
  flex: 1 1 auto;
  min-width: 740px;
  overflow: hidden;
  margin-right: 16px;
  text-overflow: ellipsis;
}

.qlist__head-assignee {
  flex: 0 0 116px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qlist__head-updated {
  flex: 0 0 88px;
  overflow: hidden;
  margin-left: 16px;
  text-align: right;
  text-overflow: ellipsis;
}

.qlist__head-next {
  flex: 0 0 auto;
  margin-left: 16px;
}

.qlist__foot {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-height: 40px;
  padding: 0 20px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

/* 骨架行：和真行同一套几何（61px = 9 + 20 + 4 + 18 + 9 + 1），只是每一格里换成一根
   12px 的骨条。 */
.qskel {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  box-sizing: border-box;
  height: 61px;
  padding: 9px 20px 9px 16px;
  border-bottom: 1px solid var(--line);
}

.qskel:last-child {
  border-bottom: 0;
}

.qskel__rail {
  flex: 0 0 4px;
  height: 40px;
  background: var(--fill-2);
}

.qskel__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 4px;
  min-width: 740px;
  margin: 0 16px 0 12px;
}

.qskel__assignee {
  flex: 0 0 116px;
}

.qskel__updated {
  flex: 0 0 88px;
  margin-left: 16px;
}

.qskel__action {
  flex: 0 0 auto;
  margin-left: 16px;
}

.qskel__bone {
  display: block;
  height: 12px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.qskel__bone--title {
  width: 62%;
}

.qskel__bone--meta {
  width: 40%;
}

.qskel__bone--assignee {
  width: 72px;
}

.qskel__bone--updated {
  width: 48px;
  margin-left: auto;
}

.qskel__bone--action {
  width: 64px;
  height: 24px;
}

/* 空态。形状和总表的空态一样（宽 320px 居中、主副两行、间距 8px），两处说的是同一件
   事 —— 「还没有人提交反馈」在两个视图里长得不一样的话，人就得分别去认。顶部留 96px
   是总表的数字（§9.2），这里跟着走，队列自己没有第二个数。 */
.qlist__none {
  flex: 0 0 auto;
  padding-top: 96px;
}

.qlist__none-box {
  width: 320px;
  margin: 0 auto;
}

.qlist__none-title {
  margin: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.qlist__none-desc {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
