<script setup lang="ts">
// 发题页挂进新外壳的那一层。
//
// 底下就是老树里那一页（`spaces/detail/PublishTask.vue`），字段一个没动：PDF 解析
// 预览与批量发布、模板、域名组、第四批的「附件（可选）」卡片、表单都在原处。
//
// 这一层要补的是**那一页读不到、但老树里由空间壳替它装好的东西**：
//
// - 它从 pinia 的 `space` store 拿空间（`currentSpaceId`、`templates`），不是从路由
//   参数拿。老树里装这份的是空间壳 `views/spaces/Detail.vue`（`fetchSpace` +
//   `fetchCategories`）；新外壳只把空间装进自己的 `board/store.ts`，pinia 那份没人管。
// - 顺序是有意义的：那一页挂载时立刻 `fetchCategories()`，而这个方法在
//   `currentSpaceId` 为空时**直接返回**（不是报错），所以必须**装完再挂**，
//   否则分类下拉是空的、模板也取不到，页面看着像「这块板什么都没有」。
//
// 另外 provide 发完题落到哪一页：老树是「我发布的」，新外壳是它自己的「我的」
// （那一页里「我发布的」只是其中一块）。
import { computed, provide, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { BOARD_PUBLISH_DONE_ROUTE } from '../routeNames'

import { PUBLISH_DONE_ROUTE } from '@/lib/shellRouteNames'
import { useSpaceStore } from '@/stores/space'
import PublishTaskView from '@/views/spaces/detail/PublishTask.vue'

provide(PUBLISH_DONE_ROUTE, BOARD_PUBLISH_DONE_ROUTE)

const route = useRoute()
const spaceStore = useSpaceStore()

const spaceId = computed(() => Number(route.params.spaceId))
/** 装好之前不挂那一页 —— 理由见顶部第二点。 */
const ready = ref(false)

watch(
  spaceId,
  async (id) => {
    if (!Number.isFinite(id) || id <= 0) return
    ready.value = false
    await spaceStore.fetchSpace(id)
    await spaceStore.fetchCategories()
    ready.value = true
  },
  { immediate: true }
)
</script>

<template>
  <PublishTaskView v-if="ready" />
</template>
