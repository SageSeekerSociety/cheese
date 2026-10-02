<script setup lang="ts">
import type { PulseRow, StatsKind } from '@/lib/adminStats'

import { useI18n } from 'vue-i18n'

// 看板的**分类导轨**：这一页的第一眼（每一块最该被看见的那一个数摆在一起）。短值、
// 状态点、提示句都由页面算好递进来 —— 这一件只画；点了只往上发事件，切哪一类、拉哪一份
// 是页面的事。
defineProps<{
  /** 分类的顺序就是这里的顺序（和服务端那几条接口一一对应）。 */
  kinds: StatsKind[]
  /** 分类名 → 词条键（字面量表在取数那一半，理由见那边的 `TAB_KEY`）。 */
  tabs: Record<string, string>
  /** 分类名 → 页签的 `title`（口径提示句 + 那一类的更新时刻）。 */
  titles: Record<string, string>
  /** 分类名 → 导轨短值。 */
  pulse: Record<string, PulseRow>
  /** 当前停在哪一类。 */
  current: StatsKind
}>()

const emit = defineEmits<{
  /** 切分类。 */
  select: [kind: StatsKind]
}>()

const { t } = useI18n()
</script>

<template>
  <!-- 分类控件是这一页的**第一个控件**，也是**唯一**一条目的地导轨：读的人先决定
           看哪一类，再看数字。下划线页签（不是迷你卡）：七张卡片把「页面里又嵌了一个
           仪表盘」，切换控件该安静地待在页首。每一块最该被看见的那个数（短值）与状态
           点留在页签上 —— 第一信息层没丢，丢的只是框。

           短值是**附属读数**，不是这个按钮的可访问名字：`aria-hidden` 掉它和状态点，
           按钮的 accessible name 保持裸标签（`交付` / `用量` …）。否则 e2e 里
           `getByRole('button', { name: '反馈', exact: true })` 会因为名字变成
           「反馈 待分诊 3」而永远匹配不上。提示句放在 `title` 上，够指针用户读。
           窄了横向滚动不换行 —— 换行会把页签堆成一面墙。 -->
  <div class="ad__kinds" role="group" :aria-label="t('feedback.dashboard.kindsAria')">
    <button
      v-for="k in kinds"
      :key="k"
      type="button"
      class="ad__kind"
      :class="{ 'ad__kind--on': k === current }"
      :aria-pressed="k === current"
      :title="titles[k]"
      @click="emit('select', k)"
    >
      <span
        v-if="pulse[k] && pulse[k]!.tone !== 'ink'"
        class="ad__kind-dot"
        :class="`ad__kind-dot--${pulse[k]!.tone}`"
        aria-hidden="true"
      />
      <span class="ad__kind-label">{{ t(tabs[k]) }}</span>
      <span class="ad__kind-val t-num" :class="`ad__kind-val--${pulse[k]!.tone}`" aria-hidden="true">{{
        pulse[k]!.value
      }}</span>
    </button>
  </div>
</template>

<style scoped>
/* 分类页签：页头下面、正文的第一行，下沿用 `--line` 分隔内容与导航。窄了横向滚动（不换行 —— 换行会把页签堆成一面墙），
   右缘一道渐隐提示「后面还有」。 */

.ad__kinds {
  display: flex;
  flex: 0 0 auto;
  gap: 28px;
  overflow-x: auto;
  border-bottom: 1px solid var(--line);
  scrollbar-width: none;
  /* 遮罩只看 alpha，颜色本身不显示 —— 但**不能写字面量**（`color-no-hex` 拦的正是
     「一个在深色主题下不成立的颜色」），所以借 `--ink`（两种主题下都不透明）当
     「不透明」用。 */
  mask-image: linear-gradient(to right, var(--ink) calc(100% - 32px), transparent);
}

.ad__kinds::-webkit-scrollbar {
  display: none;
}

/* 一颗页签：状态点 + 标签 + 短值一行。它是**按钮**（切分类），可访问名字保持
   裸标签（短值与点 `aria-hidden`，见模板注释）。 */

.ad__kind {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  padding: 0 2px 11px;
  background: none;
  border: 0;
  color: var(--muted);
  font-size: 13.5px;
  font-weight: 600;
  line-height: var(--lh-13);
  cursor: pointer;
}

.ad__kind::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: var(--ink);
  opacity: 0;
  content: '';
}

/* 选中态 = 墨色 + 下划线。**不用琥珀** —— 后台的琥珀份额已给侧栏选中条，一屏一处。 */

.ad__kind--on {
  color: var(--ink);
}

.ad__kind--on::after {
  opacity: 1;
}

@media (hover: hover) and (pointer: fine) {
  .ad__kind:not(.ad__kind--on):hover {
    color: var(--text);
  }
}

.ad__kind:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

.ad__kind-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 状态点：6px 圆点，状态色 mark 档（真实状态，不是装饰）。 */

.ad__kind-dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
}

.ad__kind-dot--ok {
  background: var(--ok);
}

.ad__kind-dot--warn {
  background: var(--warn);
}

.ad__kind-dot--danger {
  background: var(--danger);
}

/* 短值是附属读数：小两档（11.5px）、字色压一档，不跟标签抢；警示/健康/危险
   三档改色 —— 「这块需要我」的信号。 */

.ad__kind-val {
  overflow: hidden;
  color: var(--muted);
  font-size: 11.5px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ad__kind-val--warn {
  color: var(--warn-ink);
}

.ad__kind-val--ok {
  color: var(--ok-ink);
}

.ad__kind-val--danger {
  color: var(--danger-ink);
}
</style>
