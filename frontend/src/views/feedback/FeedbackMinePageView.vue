<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import FeedbackCardView from '@/components/feedback/FeedbackCardView.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import { t } from '@/i18n'

// 「我的反馈」那一页的**画面那一半**：只吃 props、只往上发事件。
//
// 取数（`loadMine` / `markRead`）、跳转、以及「空列表该说哪句话」都在
// `FeedbackMinePage.vue` 那一半；页头那副壳（`FeedbackPageShell` + 标题 + 动作 +
// 说明）也留在那里，因为 `scroll.spec.ts` 钉着这一页的根节点就是那个壳（理由：滚动归
// 壳领）。这一半画的是壳里面的正文 —— 列表、列表非空时的失败、以及一份列表都没有时
// 的空态。分开的形状见 docs 里「容器 + 视图」那套：这一半要能被单独渲染（场景棘轮要求
// 它自己就是 A 级），所以它不能读 store、读路由，也不能渲染任何取数的子组件 —— 支持
// 按钮按下时发 `support`，由外面上报的那一半去调 store。
//
// 行直接用反馈中心那一行（FeedbackCardView）：同一条反馈在哪一页都应该长一样，两套
// 卡片迟早会在状态、标签、私密标记上分叉。
const props = defineProps<{
  /** 这一趟拉取在跑。 */
  loading: boolean
  /** 服务端的原话；空着就是这一趟没出错。 */
  error: string | null
  /** 我提的 + 我替谁提的 + 指派给我的。 */
  items: FeedbackCard[]
  hasMore: boolean
  loadingMore: boolean
  /** 服务端一共几条（不是手上几条）。 */
  total: number
  /** 空列表那副骨架的文案与动作，由容器按「为什么会空」算好 —— 这一半只画。 */
  empty: { icon: string; tone: 'neutral' | 'error'; title: string; desc: string; action: string }
}>()

const emit = defineEmits<{
  (e: 'more'): void
  (e: 'empty-action'): void
  (e: 'dismiss'): void
  (e: 'support', id: string): void
}>()
</script>

<template>
  <!-- 列表非空时的失败也要画出来，理由同反馈中心：不画的话，一次失败（比如在一条
       办完的反馈上点支持，服务端回 412）会让页面看着像什么都没发生。 -->
  <FeedbackErrorBanner v-if="props.error && props.items.length" :message="props.error" @dismiss="emit('dismiss')" />

  <!-- 骨架 3 行，和反馈中心同一档（§9.4）：两页的行一样高，一页画 4 行一页画 3 行
       会让「列表有多长」看起来是两个数。 -->
  <FeedbackList
    :loading="props.loading"
    :count="props.items.length"
    :has-more="props.hasMore"
    :loading-more="props.loadingMore"
    :shown="props.items.length"
    :total="props.total"
    @more="emit('more')"
  >
    <!-- 打开详情那条链接在行自己身上（`router-link`），这里不再接一个 `@open`
         去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
    <FeedbackCardView v-for="item in props.items" :key="item.id" :item="item" @support="emit('support', $event)" />

    <template #empty>
      <AdminEmptyState
        :title="props.empty.title"
        :desc="props.empty.desc || undefined"
        :icon="props.empty.icon"
        :tone="props.empty.tone"
        :action="props.empty.action"
        @action="emit('empty-action')"
      >
        <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，这一句说的
             是「这一次为什么没成」。 -->
        <p v-if="props.error" class="fb-empty__raw t-meta-read">{{ props.error }}</p>
      </AdminEmptyState>
    </template>

    <template #foot>{{ t('feedback.mine.foot') }}</template>
  </FeedbackList>
</template>

<style scoped>
/* 空态里那句服务端的原话。空态那副骨架（`AdminEmptyState`）把动作放在这一句上面，
   所以这里只补一点上边距。 */
.fb-empty__raw {
  margin: 8px 0 0;
}
</style>
