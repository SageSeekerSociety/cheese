<template>
  <router-view />
</template>

<script lang="ts" setup>
import { onMounted, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'

import { usePageTitle } from '@/composables/usePageTitle'

import { useSpaceStore } from '@/stores/space'

const route = useRoute()
const { setDynamicTitle } = usePageTitle()

const spaceStore = useSpaceStore()
const { currentSpace: space } = storeToRefs(spaceStore)

const getSpace = async (spaceId: number) => {
  await spaceStore.fetchSpace(spaceId)
  spaceStore.fetchCategories()
  if (space.value?.name) setDynamicTitle(space.value.name, 'SpacesDetail')
}

// 设置页改了名字，浏览器标题跟着换。
watch(
  () => space.value?.name,
  (name) => {
    if (name) setDynamicTitle(name, 'SpacesDetail')
  }
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
