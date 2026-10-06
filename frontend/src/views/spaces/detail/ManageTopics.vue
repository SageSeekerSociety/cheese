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
import type { Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import ManageTopicsView from './ManageTopicsView.vue'

import { TagsApi } from '@/network/api/tags'
import { useSpaceStore } from '@/stores/space'

const spaceStore = useSpaceStore()
const { classificationTopics } = storeToRefs(spaceStore)
const { deleteClassificationTopic, addClassificationTopics } = useSpaceData()
const { t } = useI18n()

const selectedTopics = ref<Topic[]>([])
const addTopicsDialog = ref(false)

// 话题选择器的网络调用：搜索与新建。选择器在原时机（失焦、输入变化）调这两只。
const searchTopics = async (query: string) => (await TagsApi.search(query)).data.topics
const createTopic = async (name: string) => (await TagsApi.create(name)).data.id

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
