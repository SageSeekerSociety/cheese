<template>
  <ManageTemplatesView :templates="templates" @create="createTemplate" @edit="editTemplate" @delete="deleteTemplate" />
</template>

<script setup lang="ts">
// 模板管理这一页的容器：认路去新建/编辑页、真删。画面在 `ManageTemplatesView.vue`
// （场景规则见 docs/manual/dev/scenes.md）。
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import ManageTemplatesView from './ManageTemplatesView.vue'

import { useSpaceStore } from '@/stores/space'

const router = useRouter()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId, templates } = storeToRefs(spaceStore)

const createTemplate = () => {
  router.push({ name: 'SpacesDetailCreateTemplate', params: { spaceId: currentSpaceId.value } })
}

const editTemplate = (index: number) => {
  router.push({ name: 'SpacesDetailEditTemplate', params: { spaceId: currentSpaceId.value, templateIndex: index } })
}

const deleteTemplate = async (index: number) => {
  await spaceData.deleteTemplate(index)
}
</script>
