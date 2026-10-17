<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import FeedbackMinePageView from './FeedbackMinePageView.vue'

import { useFeedbackStore } from '@/stores/feedback'

// 我的反馈 (/feedback/mine)。
//
// 为什么要有这一页：反馈中心解决的是「有没有人提过同一件事」（搜索 + 支持），
// 它回答不了「我上周提的那条现在到哪了」。没有这一页时那条路只剩两个走法 —— 记住
// 详情页的链接，或者在一屏十几条里翻。**接口一直就有**（`GET /feedback/mine`）。
//
// 清单的边界由服务端定：我提的 + 我替谁提的（agent 报的、提交人是我）+ 指派给我的。
//
// ## 这一半只做三件事：读地址、取数、发请求
//
// 版面（`FeedbackPageShell` + `FeedbackList` + `AdminEmptyState`）在同目录的
// `FeedbackMinePageView.vue` 里，那一半只收 props、只发事件（见 docs/manual/dev/scenes.md）。
// 列表、总数、未读游标、点支持、翻页、空态那颗按钮的去向，都在这里通过 store / 路由做。
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

/** 空态那一颗按钮。失败时重拉，「还没提过」时去提交页 —— 两件事都在这颗按钮上。 */
function onEmptyAction() {
  if (store.error) void store.loadMine()
  else void router.push({ name: 'FeedbackSubmit' })
}
</script>

<template>
  <FeedbackMinePageView
    :items="store.mineItems"
    :total="store.mineTotal"
    :loading="store.mineLoading"
    :loading-more="store.mineLoadingMore"
    :has-more="store.mineHasMore"
    :error="store.error"
    @empty-action="onEmptyAction"
    @more="store.loadMoreMine()"
    @dismiss-error="store.clearError()"
    @support="store.toggleSupport($event)"
  />
</template>
