<template>
  <PageHeader icon="mdi-account-group" :title="t('teams.index.title')">
    <template #tabs>
      <v-tabs color="on-surface" slider-color="primary" bg-color="transparent">
        <v-tab :to="{ name: 'HomeTeamsExplore' }">{{ t('teams.index.tabExplore') }}</v-tab>
        <v-tab :to="{ name: 'HomeTeamsMine' }">{{ t('teams.index.tabMine') }}</v-tab>
        <v-tab :to="{ name: 'HomeTeamsPending' }">{{ t('teams.index.tabPending') }}</v-tab>
      </v-tabs>
      <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" @click="createTeamDialog = true">
        {{ t('teams.index.create') }}
      </BaseButton>
    </template>
  </PageHeader>

  <router-view />

  <!-- Create team dialog -->
  <AdaptiveDialog
    v-model="createTeamDialog"
    :title="t('teams.index.create')"
    :primary-label="t('teams.index.create')"
    :cancel-label="t('teams.index.cancel')"
    :primary-loading="creating"
    @primary="submit"
  >
    <v-form>
      <v-container fluid>
        <v-row>
          <v-col cols="12" md="4" class="text-center">
            <avatar-uploader v-model="teamAvatar" />
            <p class="text-body-2 text-medium-emphasis mb-2">{{ t('teams.index.avatar') }}</p>
          </v-col>
          <v-col cols="12" md="8">
            <v-text-field
              v-model="teamName"
              autocomplete="off"
              :label="t('teams.index.name')"
              variant="outlined"
              color="primary"
              :placeholder="t('teams.index.namePlaceholder')"
              :rules="[(v) => !!v || t('teams.index.nameRequired')]"
              :error-messages="nameError"
              class="mb-4"
              rounded="md"
            ></v-text-field>

            <v-text-field
              v-model="teamHandle"
              autocomplete="off"
              :label="t('work.teamLink.address')"
              :prefix="addressPrefix"
              :hint="t('work.teamLink.createAddressHint')"
              :error-messages="handleError"
              persistent-hint
              variant="outlined"
              color="primary"
              class="mb-4"
              rounded="md"
            ></v-text-field>

            <p class="text-body-2 text-medium-emphasis mb-2">{{ t('teams.index.description') }}</p>
            <tip-tap-editor
              ref="teamDescriptionEditor"
              v-model="teamDescription"
              output="html"
              :placeholder="t('teams.index.descriptionPlaceholder')"
              class="team-description-editor rounded-md"
            />
          </v-col>
        </v-row>
      </v-container>
    </v-form>
  </AdaptiveDialog>
</template>

<script setup lang="ts">
// 「团队」页（`/teams`）**画的那一半**：页头三个页签、这一页自己的那一栏，以及「创建
// 团队」对话框。建队要的那几个接口（头像、建队、跳过去）在容器 `Index.vue` 里；这里只
// 拿容器算好的错误说给用户听，提交时把表单这一份原样发上去。
import type { JSONContent } from '@tiptap/core'

import { defineAsyncComponent, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { t } from '@/i18n'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

defineProps<{
  addressPrefix: string
  /** 撞名 / 地址不合法：由容器在失败时落到对应的那一格。 */
  nameError: string
  handleError: string
  creating: boolean
}>()

const emit = defineEmits<{
  create: [{ name: string; handle: string; description: JSONContent; avatar: File | undefined; intro: string }]
}>()

const createTeamDialog = ref(false)
const teamName = ref('')
const teamHandle = ref('')
const teamDescription = ref<JSONContent>({ type: 'doc', content: [] })
const teamDescriptionEditor = ref<InstanceType<typeof TipTapEditor>>()
const teamAvatar = ref<File | undefined>()

const submit = () => {
  // 简介是编辑器里那一段纯文本，最多 250 字 —— 和描述（JSON）是两样东西。
  let intro = teamDescriptionEditor.value?.editor?.getText() ?? ''
  if (intro.length > 250) {
    intro = `${intro.slice(0, 250)}…`
  }
  emit('create', {
    name: teamName.value,
    handle: teamHandle.value,
    description: teamDescription.value,
    avatar: teamAvatar.value,
    intro,
  })
}
</script>

<style scoped></style>
