<!--
  The rendering half of Index.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the create request and the dialog's state.
-->

<script setup lang="ts">
import type { JSONContent } from '@tiptap/core'

import { defineAsyncComponent, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { t } from '@/i18n'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const createTeamDialog = defineModel<boolean>('createTeamDialog', { required: true })
const teamName = defineModel<string>('teamName', { required: true })
const teamHandle = defineModel<string>('teamHandle', { required: true })
const teamDescription = defineModel<JSONContent>('teamDescription', { required: true })
const teamAvatar = defineModel<File | undefined>('teamAvatar', { required: true })

defineProps<{
  /** 这一趟创建还在路上：那颗主按钮转圈，点不动。 */
  creatingTeam: boolean
  /** 名字那一格的错（撞名）。 */
  teamNameError: string
  /** 地址那一格的错（被占了 / 格式不对）。 */
  teamHandleError: string
  /** 地址前缀：`<眼下这个域名>/teams/`。 */
  addressPrefix: string
}>()

const emit = defineEmits<{ 'create-team': [intro: string] }>()

/** 当前这一栏的正文由容器给：它在那儿挂 `<router-view>`。 */
defineSlots<{ default?: () => unknown }>()

const teamDescriptionEditor = ref<InstanceType<typeof TipTapEditor>>()

/** 简介那一句从正文里取：编辑器在这一半，只有这一半拿得到它的文本。 */
function introOf(): string {
  const text = teamDescriptionEditor.value?.editor?.getText() ?? ''
  return text.length > 250 ? `${text.slice(0, 250)}…` : text
}
</script>

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

  <slot />

  <!-- Create team dialog -->
  <AdaptiveDialog
    v-model="createTeamDialog"
    :title="t('teams.index.create')"
    :primary-label="t('teams.index.create')"
    :cancel-label="t('teams.index.cancel')"
    :primary-loading="creatingTeam"
    @primary="emit('create-team', introOf())"
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
              :error-messages="teamNameError"
              class="mb-4"
              rounded="md"
            ></v-text-field>

            <v-text-field
              v-model="teamHandle"
              autocomplete="off"
              :label="t('work.teamLink.address')"
              :prefix="addressPrefix"
              :hint="t('work.teamLink.createAddressHint')"
              :error-messages="teamHandleError"
              persistent-hint
              variant="outlined"
              color="primary"
              class="mb-4"
              rounded="md"
            ></v-text-field>

            <p class="text-body-2 text-medium-emphasis mb-2">{{ t('teams.index.description') }}</p>
            <!-- The editor renders inside a `<Teleport to="body">`, so classes never reach
                 its root element: these two never landed anywhere, dropping them changes nothing. -->
            <tip-tap-editor
              ref="teamDescriptionEditor"
              v-model="teamDescription"
              output="html"
              :placeholder="t('teams.index.descriptionPlaceholder')"
            />
          </v-col>
        </v-row>
      </v-container>
    </v-form>
  </AdaptiveDialog>
</template>
