<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import FeedbackErrorBanner from '@/components/feedback/FeedbackErrorBanner.vue'
import FeedbackList from '@/components/feedback/FeedbackList.vue'
import FeedbackPageShell from '@/components/feedback/FeedbackPageShell.vue'
import { t } from '@/i18n'
import { useFeedbackStore } from '@/stores/feedback'

// 我的反馈 (/feedback/mine)。
//
// 为什么要有这一页：反馈中心解决的是「有没有人提过同一件事」（搜索 + 支持），
// 它回答不了「我上周提的那条现在到哪了」。没有这一页时那条路只剩两个走法 —— 记住
// 详情页的链接，或者在一屏十几条里翻。**接口一直就有**（`GET /feedback/mine`，
// api.ts 里也包了 `listMyFeedback`），只是没有任何页面调它。
//
// 清单的边界由服务端定：我提的 + 我替谁提的（agent 报的、提交人是我）+ 指派给我的。
// 这里不按来源分栏：那需要每条带一个「为什么它在我的清单里」，服务端还没有这个字段，
// 而按 handle 在前端猜一遍等于把可见性规则抄第二份。
//
// 版面走和反馈中心同一副骨架（`FeedbackPageShell` + `FeedbackList` + `AdminEmptyState`）：
// 两页的滚动、页边距、内容宽度、列表的分隔行、空态的形状因此只有一份实现 —— 以前
// 这几条规则在两页各写一遍，漂开的方式是「两页的间距差 8px」，而没有人会同时看着两页。
//
// 行直接用反馈中心那一行（`FeedbackCard`）：同一条反馈在哪一页都应该长一样，两套
// 卡片迟早会在状态、标签、私密标记上分叉。支持按钮也照用 —— store 里三份列表都会
// 被 `_find` / `_patch` 找到，所以在这一页点支持跟中心页是同一个动作。
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
 *  「你提交的反馈会出现在这里」在这里是错的。 */
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

    <!-- 列表非空时的失败也要画出来，理由同反馈中心：不画的话，一次失败（比如在一条
         办完的反馈上点支持，服务端回 412）会让页面看着像什么都没发生。 -->
    <FeedbackErrorBanner
      v-if="store.error && store.mineItems.length"
      :message="store.error"
      @dismiss="store.clearError()"
    />

    <!-- 骨架 3 行，和反馈中心同一档（§9.4）：两页的行一样高，一页画 4 行一页画 3 行
         会让「列表有多长」看起来是两个数。 -->
    <FeedbackList
      :loading="store.mineLoading"
      :count="store.mineItems.length"
      :has-more="store.mineHasMore"
      :loading-more="store.mineLoadingMore"
      :shown="store.mineItems.length"
      :total="store.mineTotal"
      @more="store.loadMoreMine()"
    >
      <!-- 打开详情那条链接在行自己身上（`router-link`），这里不再接一个 `@open`
           去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
      <FeedbackCard v-for="item in store.mineItems" :key="item.id" :item="item" />

      <template #empty>
        <AdminEmptyState
          :title="emptyState.title"
          :desc="emptyState.desc || undefined"
          :icon="emptyState.icon"
          :tone="emptyState.tone"
          :action="emptyState.action"
          @action="onEmptyAction"
        >
          <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，这一句说的
               是「这一次为什么没成」。 -->
          <p v-if="store.error" class="fb-empty__raw t-meta-read">{{ store.error }}</p>
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
