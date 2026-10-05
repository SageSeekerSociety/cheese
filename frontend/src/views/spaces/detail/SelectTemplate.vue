<template>
  <SelectTemplateView :templates="templates" @back="goBack" @select="selectTemplate" />
</template>

<script setup lang="ts">
// 选模板这一页的容器：认路、往回走、把选中的模板带进发布页。画面在
// `SelectTemplateView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import SelectTemplateView from './SelectTemplateView.vue'

import { stepBack } from '@/lib/backOut'
import { useSpaceStore } from '@/stores/space'

const router = useRouter()
const route = useRoute()
const spaceId = Number(route.params.spaceId)

const spaceStore = useSpaceStore()
const { templates } = storeToRefs(spaceStore)

// 往回走：身后有应用内来路就退一格，没有（贴链接直接开这一页）就去这条路声明好的
// 上一级（router/spaces.ts 里 `meta.backTo`），而不是把一颗按下去没反应的按钮留在那。
const goBack = () => {
  stepBack(router, { name: 'SpacesDetailTasksList', params: { spaceId } })
}

const selectTemplate = (index: number | null) => {
  router.push({
    name: 'SpacesDetailPublishTask',
    params: { spaceId },
    query: { templateId: index === null ? 'blank' : index.toString() },
  })
}
</script>
