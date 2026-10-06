<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import FeedbackMinePageView from './FeedbackMinePageView.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'
import { useFeedbackStore } from '@/stores/feedback'

// 我的反馈 (/feedback/mine) 的**容器那一半**：取数、跳转、已读游标都留在这里，正文在
// 同目录的 `FeedbackMinePageView.vue`（只吃 props、只发事件）。这样这一页单独渲染时
// 不需要后端、路由或 store —— 场景棘轮要的就是那件事。
//
// 页头那副壳（`FeedbackPageShell` + 标题 + 动作 + 说明）留在这一半，不在视图里：
// `scroll.spec.ts` 钉着这一页的根节点就是那个壳（全站约定「滚动由每一页自己领」，壳
// 是领它的那一层）—— 壳在容器里，那条不变量照旧成立。
//
// 清单的边界由服务端定：我提的 + 我替谁提的（agent 报的、提交人是我）+ 指派给我的。
// 这里不按来源分栏：那需要每条带一个「为什么它在我的清单里」，服务端还没有这个字段，
// 而按 handle 在前端猜一遍等于把可见性规则抄第二份。
defineOptions({ name: 'FeedbackMinePage' })

const store = useFeedbackStore()
const router = useRouter()

onMounted(async () => {
  await store.loadMine()
  // **顺便推进已读游标**：这一页列出的正是未读计数统计的那批（我提的 + 我替谁提的 +
  // 指派给我的），所以「打开这一页」就是「看过它们了」。顶栏那个未读点因此会在这里
  // 消失 —— 不做这一步的话，那个点只由「打开某一条详情」推进，人看完清单它还在。
  //
  // 走整批的 `markRead` 而不是逐条：服务端那张账本是一根**游标**（`FeedbackReadState`，
  // 一人一行），逐条推进本来就不成立。只在这一趟真有数时发：没未读就没有什么可推。
  //
  // **放在加载之后**：先有数字再推进，顺序反了的话列表还没到、点先没了。
  if (store.counts.unread > 0) void store.markRead()
})

/** 空列表有两种，说的话不一样：「还没有」和「没拉到」。写成同一句「暂无反馈」的话，
 *  拉挂的那一次看起来就像「平台把你的反馈弄丢了」。
 *  失败那一句走 i18n（和反馈中心共用同一条文案 —— 同一个失败，说不出两种话）；「还没
 *  提过」那一句说的是这一页特有的边界（我提的 / 我替谁提的 / 指派给我的），中心页那句
 *  「你提交的反馈会出现在这里」在这里是错的。
 *
 *  判据（`store.error`）留在容器这一侧，是因为它是「这一次拉取成没成」这件事，而不是
 *  画什么；视图只拿到算好的那一段文案。 */
const emptyState = computed(() =>
  store.error
    ? {
        icon: 'mdi-alert-circle-outline',
        tone: 'error' as const,
        title: t('feedback.center.error.title'),
        desc: t('feedback.center.error.desc'),
        action: t('feedback.center.error.retry'),
      }
    : {
        icon: 'mdi-inbox-outline',
        tone: 'neutral' as const,
        title: t('feedback.mine.empty.title'),
        desc: '',
        action: t('feedback.mine.empty.action'),
      }
)

/** 空态那一颗按钮。失败时重拉，「还没提过」时去提交页 —— 两件事都在这颗按钮上。 */
function onEmptyAction() {
  if (store.error) void store.loadMine()
  else void router.push({ name: 'FeedbackSubmit' })
}
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
        <span v-if="!store.mineLoading && !store.error">
          {{ t('feedback.mine.total', { n: store.mineTotal }) }}
        </span>
        {{ t('feedback.mine.ledeRest') }}
      </p>
    </template>

    <FeedbackMinePageView
      :loading="store.mineLoading"
      :error="store.error"
      :items="store.mineItems"
      :has-more="store.mineHasMore"
      :loading-more="store.mineLoadingMore"
      :total="store.mineTotal"
      :empty="emptyState"
      @more="store.loadMoreMine()"
      @empty-action="onEmptyAction"
      @dismiss="store.clearError()"
      @support="store.toggleSupport($event)"
    />
  </FeedbackPageShell>
</template>

<style scoped>
/* 这一页的说明。行距用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的
   倍数在两个主题、两种语言里都不会跟着别处一起调。 */
.fb-lede {
  margin: 0 0 16px;
  line-height: var(--lh-14-loose);
}
</style>
