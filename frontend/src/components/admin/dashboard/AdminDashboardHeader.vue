<script setup lang="ts">
import type { PulseRow, StatsDays, StatsKind } from '@/lib/adminStats'

import { useI18n } from 'vue-i18n'

import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'

// 页头那一块：标题、统计窗口 7/30/90、更新时间戳，以及**分类导轨**。
//
// 导轨是这一页的第一眼（每一块最该被看见的那一个数摆在一起）。短值、状态点、提示句
// 都由页面算好递进来 —— 这一件只画；点了只往上发事件，切哪一类、拉哪一份是页面的事。
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
  /** 这一类的数有「过去 N 天」这个说法吗（性能读进程内存、集成是存量）。 */
  windowed: boolean
  /** 当前窗口（天）。 */
  days: StatsDays
  /** 「更新于 HH:MM」。 */
  stamp: string
}>()

const emit = defineEmits<{
  /** 切分类。 */
  select: [kind: StatsKind]
  /** 换窗口（7/30/90）。 */
  setDays: [days: StatsDays]
}>()

const { t } = useI18n()
</script>

<template>
  <!-- 这一屏的模板从 `AdminDashboardPage.vue` 原样搬过来：DOM 结构、类名、
     `aria-*`、注释都没有动（拆的是文件，不是页面）。 -->
  <AdminPageHeader :title="t('feedback.dashboard.title')" :sub="t('feedback.dashboard.sub')">
    <template #tools>
      <!-- 统计窗口 7/30/90。只有窗口类（`WINDOWED_KINDS`）给这个切换器：
               性能读进程内存、集成是存量，它们没有「过去 N 天」—— 摆着是个假开关。
               切窗口由 `setStatsDays` 把已加载的窗口类全部重拉（缓存键=窗口）。
               形状按 `AdminTabs` 的 `sm` 档（30px 高、12.5px、琥珀下划线），但**没有**
               换成那个组件：e2e 是按 `getByRole('button', { name: '30 天' })` 点它的
               （`AdminTabs` 渲染的是 `role="tab"`），换组件就得连 e2e 一起改。 -->
      <div v-if="windowed" class="ad__wintabs" role="group" :aria-label="t('feedback.dashboard.window.switchAria')">
        <button
          type="button"
          class="ad__wintab"
          :class="{ 'ad__wintab--on': days === 7 }"
          :aria-pressed="days === 7"
          @click="emit('setDays', 7)"
        >
          {{ t('feedback.dashboard.window.d7') }}
        </button>
        <button
          type="button"
          class="ad__wintab"
          :class="{ 'ad__wintab--on': days === 30 }"
          :aria-pressed="days === 30"
          @click="emit('setDays', 30)"
        >
          {{ t('feedback.dashboard.window.d30') }}
        </button>
        <button
          type="button"
          class="ad__wintab"
          :class="{ 'ad__wintab--on': days === 90 }"
          :aria-pressed="days === 90"
          @click="emit('setDays', 90)"
        >
          {{ t('feedback.dashboard.window.d90') }}
        </button>
      </div>
      <span class="ad__stamp t-meta-read">{{ stamp }}</span>
    </template>

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
  </AdminPageHeader>
</template>

<style scoped>
/* 页头三件套（标题 / 窗口切换 / 时间戳）里最不重要的一个：窄屏让位（截断），
   不把页头撑出横向滚动。 */

.ad__stamp {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 切换控件只有一种语言：下划线小页签。窗口切换是小号版（12.5px），分类页签是
   正文版（13.5px）；激活 = 墨色 + 2px 下划线，未激活 = 灰。整页没有框状切换钮。 */

.ad__wintabs {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 4px;
}

/* 尺子抄 `AdminTabs` 的 `sm` 档：30px 高、12.5px、左右 8px 内边距、选中的那条
   2px 下划线压在容器下沿。整页的切换控件因此只有一种尺寸语言。 */

.ad__wintab {
  position: relative;
  box-sizing: border-box;
  height: 30px;
  padding: 0 8px;
  background: none;
  border: 0;
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 600;
  line-height: var(--lh-12);
  cursor: pointer;
}

.ad__wintab::after {
  position: absolute;
  right: 8px;
  bottom: 0;
  left: 8px;
  height: 2px;
  background: var(--ink);
  opacity: 0;
  content: '';
}

.ad__wintab--on {
  color: var(--ink);
  font-weight: 600;
}

.ad__wintab--on::after {
  opacity: 1;
}

@media (hover: hover) and (pointer: fine) {
  .ad__wintab:not(.ad__wintab--on):hover {
    color: var(--text);
  }
}

.ad__wintab:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 分类页签：页头里那把工具槽的下一行（`AdminPageHeader` 的默认插槽给 12px 间距），
   下沿用 `--line` 分隔内容与导航。窄了横向滚动（不换行 —— 换行会把页签堆成一面墙），
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
