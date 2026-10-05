<template>
  <!-- 画在「分类」那一块下面，隔开一段。 -->
  <SettingsToolbar :title="t('spaces.settings.sections.topics')" class="mt-8">
    <BaseButton kind="primary" prepend-icon="mdi-plus" @click="addTopicsDialog = true">
      {{ t('spaces.detail.manageTopics.addTopics') }}
    </BaseButton>
  </SettingsToolbar>

  <AdaptiveDialog
    v-model="addTopicsDialog"
    :title="t('spaces.detail.manageTopics.addTopics')"
    :primary-label="t('spaces.detail.manageTopics.add')"
    size="lg"
    @primary="emit('confirmAdd')"
  >
    <TopicSelectorView
      v-model="selectedTopics"
      :search-topics="searchTopics"
      :create-topic="createTopic"
      always-adding
    />
  </AdaptiveDialog>

  <div class="settings-card">
    <v-list v-if="classificationTopics.length > 0" class="settings-list" bg-color="transparent">
      <v-list-item v-for="(topic, index) in classificationTopics" :key="index" :title="topic.name">
        <template #append>
          <BaseButton
            kind="ghost"
            icon="mdi-delete-outline"
            size="sm"
            :aria-label="t('spaces.detail.manageTopics.delete')"
            @click="emit('delete', topic.id)"
          />
        </template>
      </v-list-item>
    </v-list>

    <BaseEmptyState v-else size="inline" class="settings-empty" :title="t('spaces.detail.manageTopics.noTopics')" />
  </div>
</template>

<script setup lang="ts">
import type { Topic } from '@/types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import TopicSelectorView from '@/components/common/TopicSelectorView.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'

defineProps<{
  classificationTopics: Topic[]
  /** 搜索话题，供选择器在其原时机调用。 */
  searchTopics: (query: string) => Promise<{ id: number; name: string }[]>
  /** 按名新建话题，供选择器在其原时机调用。 */
  createTopic: (name: string) => Promise<number>
}>()

const emit = defineEmits<{
  delete: [id: number]
  confirmAdd: []
}>()

const selectedTopics = defineModel<Topic[]>('selectedTopics', { required: true })
const addTopicsDialog = defineModel<boolean>('addTopicsDialog', { required: true })

const { t } = useI18n()
</script>

<style scoped src="@/styles/settings-card.css"></style>
