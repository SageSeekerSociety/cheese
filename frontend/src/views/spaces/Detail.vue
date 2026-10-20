<template>
  <!-- 空间下每一页都在这里。外面套一层兜底：某页在渲染里抛错时，Vue 会把它整棵子树
       换成空注释、那一块直接白掉（「待审核」曾经就是这样：模块求值时抛错，整页连同
       侧栏一起没了）。ErrorBoundary 把这一块换成一句提示加一颗「重试」，别的部分照旧；
       reset-key 用当前路由，换一页或换一个空间时那层自己活过来。 -->
  <ErrorBoundary :reset-key="route.path">
    <!-- Pages mount once the space's detail is back: an unreviewed space answers 404 to
         everything but its detail, and that one is sent to its application page instead
         (see getSpace). When the detail could not be read at all, the page still mounts
         and says so itself. -->
    <router-view v-if="settled && !underReview" />
  </ErrorBoundary>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { usePageTitle } from '@/composables/usePageTitle'
import { useSpaceData } from '@/composables/useSpaceData'

import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const router = useRouter()
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
  // 还没过审的空间只有申请人自己读得到，里面每一页都还打不开：换到这份申请上。
  if (underReview.value) {
    void router.replace({ name: 'HomeSpaceApplication', params: { spaceId: String(spaceId) } })
    return
  }
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
