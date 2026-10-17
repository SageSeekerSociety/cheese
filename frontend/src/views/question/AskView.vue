<!--
  The rendering half of Ask.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the requests and the stores.
-->

<script setup lang="ts">
import type { default as EditorJS, EditorConfig } from '@editorjs/editorjs'
import type { BaseFieldProps } from 'vee-validate'
import type { Topic } from '@/types/commons'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import TopicSelectorView from '@/components/common/TopicSelectorView.vue'

defineProps<{
  titleProps: BaseFieldProps & { 'error-messages': any; error: boolean }
  editorConfig: EditorConfig
  topicsProps: BaseFieldProps & { 'error-messages': any; error: boolean }
  searchTopics: (query: string) => Promise<Topic[]>
  createTopic: (name: string) => Promise<number>
  hasBounty: boolean
  bountyProps: BaseFieldProps & { 'error-messages': any; error: boolean }
  isSubmitting: boolean
  submit: (e?: Event) => Promise<Promise<void> | undefined>
}>()
const emit = defineEmits<{
  create: [editorInstance: EditorJS]
  'remove-bounty': []
  'add-bounty': []
}>()
const title = defineModel<string | undefined>('title', { required: true })
const topics = defineModel<{ id: number; name: string }[] | undefined>('topics', { required: true })
const bounty = defineModel<number | undefined>('bounty', { required: true })
const { t } = useI18n()
</script>

<template>
  <v-container>
    <v-row>
      <v-col cols="12" md="8" lg="9">
        <v-sheet rounded="lg" class="pa-4">
          <v-form @submit.prevent>
            <v-text-field
              v-model="title"
              autocomplete="off"
              :label="t('questions.ask.titleLabel')"
              variant="plain"
              class="question-title-input"
              hide-details="auto"
              :single-line="true"
              v-bind="titleProps"
            ></v-text-field>
            <rich-editor holder="editor" :config="editorConfig" @create="emit('create', $event)" />
            <topic-selector-view
              v-model="topics"
              class="mt-4"
              v-bind="topicsProps"
              :max="5"
              :search-topics="searchTopics"
              :create-topic="createTopic"
            />
            <div class="d-flex align-center" style="gap: 16px">
              <BaseButton
                kind="secondary"
                :prepend-icon="hasBounty ? 'mdi-currency-usd-off' : 'mdi-currency-usd'"
                @click="hasBounty ? emit('remove-bounty') : emit('add-bounty')"
              >
                {{ hasBounty ? t('questions.ask.removeBounty') : t('questions.ask.addBounty') }}
              </BaseButton>
              <v-slide-x-reverse-transition mode="out-in">
                <v-slider
                  v-if="hasBounty"
                  v-model="bounty"
                  thumb-label="always"
                  v-bind="bountyProps"
                  min="1"
                  max="20"
                  step="1"
                  show-ticks
                  hide-details
                  class="flex-auto"
                >
                  <template #append>
                    <span style="vertical-align: baseline; min-width: 5rem; text-align: end">
                      <span>{{ t('questions.ask.bounty', { bounty }) }} </span><v-icon>mdi-cheese</v-icon>
                    </span>
                  </template>
                </v-slider>
              </v-slide-x-reverse-transition>
            </div>
          </v-form>
        </v-sheet>
      </v-col>

      <v-col cols="12" md="4" lg="3">
        <BaseButton kind="primary" block :loading="isSubmitting" class="mb-4" @click="submit">
          {{ t('questions.ask.submit') }}
        </BaseButton>
        <v-sheet rounded="lg" class="pa-4 mb-4">
          <div class="text-h5">{{ t('questions.ask.guide.title') }}</div>
          <p>{{ t('questions.ask.guide.tried') }}</p>
          <p>{{ t('questions.ask.guide.effort') }}</p>
          <p>{{ t('questions.ask.guide.premise') }}</p>
          <p>{{ t('questions.ask.guide.willing') }}</p>
        </v-sheet>
      </v-col>
    </v-row>
  </v-container>
</template>

<style>
.ce-block__content,
.ce-toolbar__content {
  max-width: unset;
}

.ce-block__content {
  padding: 0;
}

.question-title-input {
  font-weight: bold;
}
.question-title-input .v-field__input,
.question-title-input .v-field-label {
  font-size: 1.5rem;
}

.question-topics {
  gap: 8px;
}

.question-topic-input {
  width: 240px;
}
</style>
