<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import FeedbackCard from '@/components/feedback/FeedbackCard.vue'
import SubmitFeedbackDrawer from '@/components/feedback/SubmitFeedbackDrawer.vue'
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
const router = useRouter()

onMounted(() => {
  void store.loadMine()
})

function onSubmitted(id: string) {
  void router.push(`/feedback/${id}`)
}
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
        <v-btn color="primary" prepend-icon="mdi-plus" @click="store.openSubmit()">提交反馈</v-btn>
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

      <LoadingSkeleton v-if="store.mineLoading" variant="feedback" :rows="4" />

      <div v-else class="fb-list">
        <!-- 打开详情那条链接在卡片自己身上（`router-link`），这里不再接一个
             `@open` 去 push —— 那就又回到「只有鼠标够得着」了，见 FeedbackCard.vue。 -->
        <FeedbackCard v-for="item in store.mineItems" :key="item.id" :item="item" />
        <!-- 空列表有两种，说的话不一样：「还没有」和「没拉到」。写成同一句
             「暂无反馈」的话，拉挂的那一次看起来就像「平台把你的反馈弄丢了」。 -->
        <div v-if="!store.mineItems.length" class="fb-empty">
          <v-icon size="28" class="mb-2">
            {{ store.error ? 'mdi-alert-circle-outline' : 'mdi-inbox-outline' }}
          </v-icon>
          <div class="t-body">
            {{ store.error ? store.error : '你还没有提过反馈，也没有指派给你的' }}
          </div>
          <v-btn v-if="store.error" variant="text" color="secondary" size="small" @click="store.loadMine()">
            重试
          </v-btn>
          <v-btn v-else variant="text" color="secondary" size="small" @click="store.openSubmit()">提交一条</v-btn>
        </div>
      </div>

      <p v-if="!store.mineLoading" class="t-meta fb-foot">
        办完的反馈（已修复、已上线）留在列表里，但不再接受支持；私密的那几种只有你和管理员看得到
      </p>
    </div>

    <SubmitFeedbackDrawer @submitted="onSubmitted" />
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
  line-height: 1.7;
}
.fb-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.fb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 48px 0;
  color: var(--faint);
}
.fb-foot {
  margin: 24px 0 0;
  line-height: 1.7;
}
</style>
