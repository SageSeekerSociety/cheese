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
            <rich-editor holder="editor" :config="editorConfig" @create="onCreate" />
            <topic-selector v-model="topics" class="mt-4" v-bind="topicsProps" :max="5" />
            <div class="d-flex align-center" style="gap: 16px">
              <BaseButton
                kind="secondary"
                :prepend-icon="hasBounty ? 'mdi-currency-usd-off' : 'mdi-currency-usd'"
                @click="hasBounty ? removeBounty() : addBounty()"
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

<script setup lang="ts">
import type EditorJS from '@editorjs/editorjs'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { defaultEditorConfig } from '@/utils/editor'
import { vuetifyConfig } from '@/utils/form'

import BaseButton from '@/components/base/BaseButton.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import TopicSelector from '@/components/common/TopicSelector.vue'
import { QuestionApi } from '@/network/api/questions'

let editor: EditorJS
const { t } = useI18n()
const editorConfig = defaultEditorConfig()
const router = useRouter()
const route = useRoute()

const groupId = computed(() => route.query.groupId)

// watch(groupId, (groupId) => {
//   if (groupId) {
//     topics.value = [{ id: groupId as number, name: '' }]
//   }
// })

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      title: z
        .string()
        .trim()
        .refine((v) => v.length > 0, {
          message: t('questions.ask.errors.titleRequired'),
        })
        .refine((v) => v.endsWith('?') || v.endsWith('？'), {
          message: t('questions.ask.errors.titleQuestionMark'),
        }),
      topics: z
        .object({
          id: z.number(),
          name: z.string(),
        })
        .array()
        .refine((v) => v.length > 0, {
          message: t('questions.ask.errors.topicRequired'),
        }),
      bounty: z.number().min(0).max(20).default(0),
    })
  ),
})
const [title, titleProps] = defineField('title', vuetifyConfig)
const [topics, topicsProps] = defineField('topics', vuetifyConfig)
const [bounty, bountyProps] = defineField('bounty', vuetifyConfig)

const onCreate = (editorInstance: EditorJS) => {
  editor = editorInstance
}

const hasBounty = ref(false)

const addBounty = () => {
  hasBounty.value = true
  bounty.value = 1
}

const removeBounty = () => {
  hasBounty.value = false
  bounty.value = 0
}

const submit = handleSubmit(async (values) => {
  const outputData = await editor.save()
  const content = JSON.stringify(outputData)
  const res = await QuestionApi.ask({
    title: values.title,
    content,
    type: 0,
    topics: values.topics.map((topic) => topic.id),
    bounty: bounty.value ?? 0,
  })
  toast.success(t('questions.ask.success'))
  router.push({ name: 'QuestionAnswerList', params: { questionId: res.data.id } })
})
</script>

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
