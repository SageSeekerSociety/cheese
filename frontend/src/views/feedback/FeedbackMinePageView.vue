<script setup lang="ts">
import type { FeedbackCard } from '@/cx_types'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import FeedbackCardView from '@/components/feedback/FeedbackCardView.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'

// 「我的反馈」那一页的**画面那一半**：只吃 props、只往上发事件。
//
// 取数（`loadMine` / `markRead`）、跳转、以及「空列表该说哪句话」都在
// `FeedbackMinePage.vue` 那一半。分开的形状见 docs 里「容器 + 视图」那套：这一页会被
// 单独渲染（场景棘轮要求它自己就是 A 级），所以它不能自己去读 store、读路由。
//
// ## 为什么要有「我的反馈」这一页
//
// 反馈中心解决的是「有没有人提过同一件事」（搜索 + 支持），它回答不了「我上周提的那条
// 现在到哪了」。没有这一页时那条路只剩两个走法 —— 记住详情页的链接，或者在一屏十几条里
// 翻。**接口一直就有**（`GET /feedback/mine`，api.ts 里也包了 `listMyFeedback`），只是
// 没有任何页面调它。
//
// ## 版面
//
// 走和反馈中心同一副骨架（`FeedbackPageShell` + `FeedbackList` + `AdminEmptyState`）：
// 两页的滚动、页边距、内容宽度、列表的分隔行、空态的形状因此只有一份实现 —— 以前这几条
// 规则在两页各写一遍，漂开的方式是「两页的间距差 8px」，而没有人会同时看着两页。
//
// 行直接用反馈中心那一行（`FeedbackCard`）：同一条反馈在哪一页都应该长一样，两套卡片
// 迟早会在状态、标签、私密标记上分叉。支持按钮也照用 —— store 里三份列表都会被
// `_find` / `_patch` 找到，所以在这一页点支持跟中心页是同一个动作（按下时发 `support`，
// 由外面上报的那一半去调 store）。
const props = defineProps<{
  /** 这一趟拉取在跑。 */
  loading: boolean
  /** 服务端的原话；空着就是这一趟没出错。 */
  error: string | null
  /** 我提的 + 我替谁提的 + 指派给我的。 */
  items: FeedbackCard[]
  hasMore: boolean
  loadingMore: boolean
  /** 服务端一共几条（不是手上几条）。加载中不画它。 */
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
  <FeedbackPageShell :title="t('feedback.mine.title')">
    <template #actions>
      <BaseButton size="sm" to="/feedback">
        {{ t('feedback.mine.back') }}
      </BaseButton>
      <BaseButton kind="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">
        {{ t('feedback.mine.submit') }}
      </BaseButton>
    </template>

    <!-- 这一页的说明。**总数只在拉到之后才说**：加载中写「共 0 条」是在报一个还不知道
         的数。 -->
    <template #sub>
      <p class="t-meta-read t-num fb-lede">
        {{ t('feedback.mine.lede') }}
        <span v-if="!props.loading && !props.error">
          {{ t('feedback.mine.total', { n: props.total }) }}
        </span>
        {{ t('feedback.mine.ledeRest') }}
      </p>
    </template>

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
  </FeedbackPageShell>
</template>

<style scoped>
/* 这一页的说明。行距用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的
   倍数在两个主题、两种语言里都不会跟着别处一起调。 */
.fb-lede {
  margin: 0 0 16px;
  line-height: var(--lh-14-loose);
}
/* 空态里那句服务端的原话。空态那副骨架（`AdminEmptyState`）把动作放在这一句上面，
   所以这里只补一点上边距。 */
.fb-empty__raw {
  margin: 8px 0 0;
}
</style>
