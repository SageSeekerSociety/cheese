<template>
  <router-view />
</template>

<script lang="ts" setup>
import { onMounted, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

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
