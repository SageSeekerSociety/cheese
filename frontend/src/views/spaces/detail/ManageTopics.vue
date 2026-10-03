<template>
  <!-- 画在「分类」那一块下面，隔开一段。 -->
  <SettingsToolbar :title="t('spaces.settings.sections.topics')" class="mt-8">
    <BaseButton kind="primary" prepend-icon="mdi-plus">
      {{ t('spaces.detail.manageTopics.addTopics') }}

      <v-dialog v-model="addTopicsDialog" activator="parent" width="800">
        <template #default="{ isActive }">
          <v-card>
            <v-card-title>{{ t('spaces.detail.manageTopics.addTopics') }}</v-card-title>
            <v-card-text>
              <topic-selector v-model="selectedTopics" always-adding />
            </v-card-text>
            <v-card-actions>
              <BaseButton kind="ghost" @click="isActive.value = false">
                {{ t('spaces.detail.manageTopics.cancel') }}
              </BaseButton>
              <BaseButton kind="primary" @click="confirmAddTopics">
                {{ t('spaces.detail.manageTopics.add') }}
              </BaseButton>
            </v-card-actions>
          </v-card>
        </template>
      </v-dialog>
    </BaseButton>
  </SettingsToolbar>
  <div class="settings-card">
    <v-list v-if="classificationTopics.length > 0" class="settings-list" bg-color="transparent">
      <v-list-item v-for="(topic, index) in classificationTopics" :key="index" :title="topic.name">
        <template #append>
          <BaseButton
            kind="ghost"
            icon="mdi-delete-outline"
            size="sm"
            :aria-label="t('spaces.detail.manageTopics.delete')"
            @click="deleteClassificationTopic(topic.id)"
          />
        </template>
      </v-list-item>
    </v-list>

    <p v-else class="settings-empty">{{ t('spaces.detail.manageTopics.noTopics') }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import BaseButton from '@/components/base/BaseButton.vue'
import TopicSelector from '@/components/common/TopicSelector.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { useSpaceStore } from '@/stores/space'
import { Topic } from '@/types'

const spaceStore = useSpaceStore()
const { classificationTopics } = storeToRefs(spaceStore)
const { deleteClassificationTopic, addClassificationTopics } = useSpaceData()
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

<style scoped src="@/styles/settings-card.css"></style>
