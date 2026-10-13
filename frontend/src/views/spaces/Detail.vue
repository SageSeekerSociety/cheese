<template>
  <!-- 空间下每一页都在这里。外面套一层兜底：某页在渲染里抛错时，Vue 会把它整棵子树
       换成空注释、那一块直接白掉（「待审核」曾经就是这样：模块求值时抛错，整页连同
       侧栏一起没了）。ErrorBoundary 把这一块换成一句提示加一颗「重试」，别的部分照旧；
       reset-key 用当前路由，换一页或换一个空间时那层自己活过来。 -->
  <ErrorBoundary :reset-key="route.path">
    <SpaceReviewNoticeView v-if="underReview" :status="underReview" :reason="space?.reviewReason" />
    <!-- 页面等这块板的详情回来再挂：没过审的板除详情外一律 404，先挂上去，每一页
         各自取数、各自弹一条红色的「获取失败」。详情没取到（不存在、不是成员）时
         照旧挂上，由页面自己说。 -->
    <router-view v-else-if="settled" />
  </ErrorBoundary>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { usePageTitle } from '@/composables/usePageTitle'
import { useSpaceData } from '@/composables/useSpaceData'

import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import { useSpaceStore } from '@/stores/space'
import SpaceReviewNoticeView from '@/views/spaces/detail/SpaceReviewNoticeView.vue'

const route = useRoute()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space, currentSpaceId, isManager } = storeToRefs(spaceStore)

const routeSpaceId = computed(() => Number(route.params.spaceId))
/** 详情已经回来（成功或失败）的那块板。 */
const settledId = ref<number | null>(null)
const settled = computed(() => settledId.value === routeSpaceId.value || space.value?.id === routeSpaceId.value)
/** 还没过审（待审核或被驳回）时是哪一种；过审了或还不知道就是 null。只有所有者读得到这样一块板。 */
const underReview = computed<'PENDING' | 'REJECTED' | null>(() => {
  const current = space.value
  if (current?.id !== routeSpaceId.value) return null
  const status = current.reviewStatus
  return status === 'PENDING' || status === 'REJECTED' ? status : null
})

const getSpace = async (spaceId: number) => {
  await spaceData.fetchSpace(spaceId)
  settledId.value = spaceId
  if (!underReview.value) spaceData.fetchCategories()
  if (space.value?.name) setDynamicTitle(space.value.name, 'SpacesDetail')
}

// 设置页改了名字，浏览器标题跟着换。
watch(
  () => space.value?.name,
  (name) => {
    if (name) setDynamicTitle(name, 'SpacesDetail')
  }
)

// 侧栏「待审核」旁边的数：只有所有者与管理员读得到（接口对别人不开），换了空间重读一次。
watch(
  [currentSpaceId, isManager],
  ([id, manager]) => {
    if (id && manager && !underReview.value) spaceData.fetchPendingAuditCount()
  },
  { immediate: true }
)

onMounted(async () => {
  await getSpace(Number(route.params.spaceId))
})

onBeforeRouteUpdate(async (to, from) => {
  if (to.params.spaceId !== from.params.spaceId) {
    await getSpace(Number(to.params.spaceId))
  }
})
</script>
