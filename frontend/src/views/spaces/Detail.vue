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
  <!-- 进这块板时那张告知卡：这块板开着学情分析、我还没点过「我已阅读并知悉」就弹一次。
       它是纯 props + emit 的一件（`components/spaces/LearningNoticeDialog.vue`），弹不弹
       由这里读到的 `needsAcknowledge` 决定，关掉只有那条按钮一条路。 -->
  <LearningNoticeDialog v-model:open="noticeOpen" :acknowledging="acknowledging" @acknowledge="onAcknowledge" />
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { usePageTitle } from '@/composables/usePageTitle'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceLearningNotice } from '@/composables/useSpaceLearningAnalysis'

import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import LearningNoticeDialog from '@/components/spaces/LearningNoticeDialog.vue'
import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const router = useRouter()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace: space, currentSpaceId, isManager, myRole } = storeToRefs(spaceStore)

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

/** 进这块板的告知卡：要不要弹、点了之后记在哪，全在 composable 里（读不到的板不弹）。 */
const { notice, acknowledge } = useSpaceLearningNotice(() =>
  // 还没读到这块板、或者它还卡在审核里时先不问：没资格的那会儿问只会换来一个 404。
  settled.value && !underReview.value ? routeSpaceId.value : undefined
)
const acknowledging = ref(false)
/** 弹不弹照服务端那一份；关掉只有「我已阅读并知悉」一条路（`persistent`），所以写入一律
 *  不收 —— 点了按钮走 `acknowledge`，那时的 `needsAcknowledge` 自己变假。 */
const noticeOpen = computed({
  get: () => notice.value?.needsAcknowledge === true,
  set: () => {},
})
async function onAcknowledge() {
  if (acknowledging.value) return
  acknowledging.value = true
  try {
    await acknowledge()
  } finally {
    acknowledging.value = false
  }
}

const getSpace = async (spaceId: number) => {
  await spaceData.fetchSpace(spaceId)
  settledId.value = spaceId
  // 还没过审的空间只有申请人自己和平台管理员读得到，里面每一页都还打不开：申请人换到
  // 这份申请上；不在名单里的是平台管理员，换到「空间申请」去审它。
  if (underReview.value) {
    void router.replace(
      myRole.value !== 'MEMBER'
        ? { name: 'HomeSpaceApplication', params: { spaceId: String(spaceId) } }
        : { name: 'AdminSpaces' }
    )
    return
  }
  spaceData.fetchCategories()
  // 侧栏的两个数：「我的草稿」有几份，上次看过题目列表之后新发了几道（正在看题目列表
  // 的话那一页自己清零）。
  void spaceData.fetchMyDraftCount()
  if (route.name !== 'SpacesDetailTasksList') void spaceData.fetchNewTaskCount()
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
