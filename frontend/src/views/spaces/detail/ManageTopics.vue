<template>
  <ManageTopicsView
    v-model:selected-topics="selectedTopics"
    v-model:add-topics-dialog="addTopicsDialog"
    :classification-topics="classificationTopics"
    :search-topics="searchTopics"
    :create-topic="createTopic"
    @delete="deleteClassificationTopic"
    @confirm-add="confirmAddTopics"
  />
</template>

<script setup lang="ts">
// 分类话题这一页的容器：读 store 里的分类话题、新增/删除，话题选择器要的搜索与新建
// 从 `useSpaceData` 取（网络层只在那一层）。画面在 `ManageTopicsView.vue`（场景规则见
// docs/manual/dev/scenes.md）。
import type { Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import ManageTopicsView from './ManageTopicsView.vue'

import { toast } from '@/lib/toast'
import { useSpaceStore } from '@/stores/space'

const spaceStore = useSpaceStore()
const { classificationTopics } = storeToRefs(spaceStore)
const { deleteClassificationTopic, addClassificationTopics, searchTopics, createTopic } = useSpaceData()
const { t } = useI18n()

const selectedTopics = ref<Topic[]>([])
const addTopicsDialog = ref(false)

const confirmAddTopics = async () => {
  try {
    await addClassificationTopics(selectedTopics.value.map((topic) => topic.id))
    selectedTopics.value = []
    addTopicsDialog.value = false
    toast.success(t('spaces.detail.manageTopics.addSuccess'))
  } catch (error) {
    toast.error(t('spaces.detail.manageTopics.addFailed'))
  }
}
</script>
