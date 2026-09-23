<script setup lang="ts">
import { computed, onMounted } from 'vue'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
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
// 卡片直接用反馈中心那一张（`FeedbackCard`）：同一条反馈在哪一页都应该长一样，两套
// 卡片迟早会在状态、标签、私密标记上分叉。支持按钮也照用 —— store 里三份列表都会
// 被 `_find` / `_patch` 找到，所以在这一页点支持跟中心页是同一个动作。
defineOptions({ name: 'FeedbackMinePage' })

const store = useFeedbackStore()

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
 *  提过」那一句留在这页自己的词表里：它说的是这一页特有的边界（我提的 / 我替谁提的 /
 *  指派给我的），中心页那句「你提交的反馈会出现在这里」在这里是错的。 */
const emptyState = computed(() =>
  store.error
    ? {
        icon: 'mdi-alert-circle-outline',
        title: t('feedback.center.error.title'),
        desc: t('feedback.center.error.desc'),
      }
    : { icon: 'mdi-inbox-outline', title: '你还没有提过反馈，也没有指派给你的', desc: '' }
)
</script>

<template>
  <!-- `fill-height overflow-y-auto` 的理由和反馈中心那一页一样（common.scss 把
       html/body/#app 定成固定高度 + overflow: hidden，滚动由每一页自己领）。 -->
  <div class="fb-page fill-height overflow-y-auto">
    <div class="fb-page__inner page-container">
      <header class="fb-head">
        <h1 class="t-page-title">我的反馈</h1>
        <v-spacer />
        <v-btn variant="text" color="secondary" size="small" to="/feedback">回反馈中心</v-btn>
        <v-btn color="primary" prepend-icon="mdi-plus" :to="{ name: 'FeedbackSubmit' }">提交反馈</v-btn>
      </header>

      <p class="t-meta fb-lede">
        这里是我提的、我替 AI 队友提的，以及指派给我的。
        <!-- 总数只在拉到之后才说：加载中写「共 0 条」是在报一个还不知道的数。 -->
        <span v-if="!store.mineLoading && !store.error">共 {{ store.mineTotal }} 条。</span>
        别人的反馈不在这里，去反馈中心搜。
      </p>

      <!-- 列表非空时的失败也要画出来，理由同反馈中心：不画的话，一次失败（比如
           在一条办完的反馈上点支持，服务端回 412）会让页面看着像什么都没发生。 -->
      <v-alert
        v-if="store.error && store.mineItems.length"
        type="warning"
        variant="tonal"
        density="compact"
        closable
        class="mb-3"
        @click:close="store.clearError()"
      >
        {{ store.error }}
      </v-alert>

      <!-- 骨架 3 张，和反馈中心同一档（§9.4）：两页的卡片一样高，一页画 4 张一页画 3 张
           会让「列表有多长」看起来是两个数。 -->
      <LoadingSkeleton v-if="store.mineLoading" variant="feedback" :rows="3" />

      <div v-else class="fb-list">
        <!-- 打开详情那条链接在卡片自己身上（`router-link`），这里不再接一个
             `@open` 去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
        <FeedbackCard v-for="item in store.mineItems" :key="item.id" :item="item" />
        <!-- 空列表有两种，说的话不一样：「还没有」和「没拉到」。写成同一句
             「暂无反馈」的话，拉挂的那一次看起来就像「平台把你的反馈弄丢了」。 -->
        <div v-if="!store.mineItems.length" class="fb-empty">
          <v-icon size="28" class="fb-empty__icon">{{ emptyState.icon }}</v-icon>
          <div class="fb-empty__title">{{ emptyState.title }}</div>
          <p v-if="emptyState.desc" class="fb-empty__desc">{{ emptyState.desc }}</p>
          <!-- 服务端那句话照直画出来：上面那句说的是「这类事现在是什么样」，这一句说的
               是「这一次为什么没成」。 -->
          <p v-if="store.error" class="fb-empty__raw t-meta-read">{{ store.error }}</p>
          <v-btn
            v-if="store.error"
            variant="text"
            color="secondary"
            size="small"
            class="fb-empty__action"
            @click="store.loadMine()"
          >
            重试
          </v-btn>
          <v-btn
            v-else
            variant="text"
            color="secondary"
            size="small"
            class="fb-empty__action"
            :to="{ name: 'FeedbackSubmit' }"
          >
            提交一条
          </v-btn>
        </div>

        <!-- 翻页那一行只在**真的还有下一页**时出现。到底了不画「已到底」：那一行字只是
             在告诉读者「这个按钮你按不了了」，而没按过的人看到它只会以为自己漏看了什么。
             计数是「你手上几条 / 一共几条」——两个数不一样重，左边的是这一页拿到的，
             右边的是服务端数出来的，读者拿它们比才知道还剩多少。 -->
        <div v-if="store.mineHasMore" class="fb-more">
          <v-btn
            variant="outlined"
            color="secondary"
            size="small"
            :loading="store.mineLoadingMore"
            @click="store.loadMoreMine()"
          >
            加载更多
          </v-btn>
          <span class="t-meta"> 已显示 {{ store.mineItems.length }} / 共 {{ store.mineTotal }} 条 </span>
        </div>
      </div>

      <p v-if="!store.mineLoading" class="t-meta fb-foot">
        办完的反馈（已修复、已上线）留在列表里，但不再接受支持；私密的那几种只有你、平台管理员、以及提出它时在那个房间里的人看得到
      </p>
    </div>
  </div>
</template>

<style scoped>
/* 这里这一组类和反馈中心那一页同名同值，但样式是 scoped 的，各自留一份。
   抽成公共样式的话这两页就不再是「各自完整的一页」了，而它们要长得一样的地方
   本来就只有这几行。 */
.fb-page {
  padding: 24px 16px 48px;
}
.fb-page__inner {
  margin: 0 auto;
}
.fb-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 12px;
}
.fb-lede {
  margin: 0 0 16px;
  /* 用 token 而不是 1.7：这一档的领值只有 --lh-* 这一份来源，手写的倍数在两个
     主题、两种语言里都不会跟着别处一起调。 */
  line-height: var(--lh-14-loose);
}
.fb-list {
  display: flex;
  flex-direction: column;
  /* 卡片之间 16px，和反馈中心同一档（§4.3）。 */
  gap: 16px;
}
/* 空态那一块：宽 320、水平居中，主文案 15/--lh-15/600/--ink，副文案 13/--lh-13/
   --muted，主副之间 8px（§9.2）。和反馈中心那一块同形，两页的空态才是同一个东西。 */
.fb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 320px;
  margin: 0 auto;
  padding: 48px 0;
  gap: 8px;
}
.fb-empty__icon {
  color: var(--muted);
}
.fb-empty__title {
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
  text-align: center;
}
.fb-empty__desc {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: center;
}
.fb-empty__raw {
  margin: 0;
  text-align: center;
  word-break: break-word;
}
.fb-empty__action {
  margin-top: 4px;
}
/* 翻页那一行：按钮和计数在同一条中线上，两者之间 12px。 */
.fb-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding-top: 4px;
}
.fb-foot {
  margin: 24px 0 0;
  line-height: var(--lh-14-loose);
}
</style>
