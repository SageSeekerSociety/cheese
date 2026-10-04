<template>
  <!-- 空间下每一页都在这里。外面套一层兜底：某页在渲染里抛错时，Vue 会把它整棵子树
       换成空注释、那一块直接白掉（「待审核」曾经就是这样：模块求值时抛错，整页连同
       侧栏一起没了）。ErrorBoundary 把这一块换成一句提示加一颗「重试」，别的部分照旧；
       reset-key 用当前路由，换一页或换一个空间时那层自己活过来。 -->
  <ErrorBoundary :reset-key="route.path">
    <router-view />
  </ErrorBoundary>
</template>

<script lang="ts" setup>
import { onMounted, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import { usePageTitle } from '@/composables/usePageTitle'
import { useSpaceData } from '@/composables/useSpaceData'

import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space, currentSpaceId, isManager } = storeToRefs(spaceStore)

const getSpace = async (spaceId: number) => {
  await spaceData.fetchSpace(spaceId)
  spaceData.fetchCategories()
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
    if (id && manager) spaceData.fetchPendingAuditCount()
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
