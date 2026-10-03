<template>
  <PageHeader icon="mdi-account-group" :title="t('teams.index.title')">
    <template #tabs>
      <v-tabs color="on-surface" slider-color="primary" bg-color="transparent">
        <v-tab :to="{ name: 'HomeTeamsExplore' }">{{ t('teams.index.tabExplore') }}</v-tab>
        <v-tab :to="{ name: 'HomeTeamsMine' }">{{ t('teams.index.tabMine') }}</v-tab>
        <v-tab :to="{ name: 'HomeTeamsPending' }">{{ t('teams.index.tabPending') }}</v-tab>
      </v-tabs>
      <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" @click="openCreateTeamDialog">
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
    :primary-loading="creatingTeam"
    @primary="createTeam"
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
import type { JSONContent } from '@tiptap/core'

import { defineAsyncComponent, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const createTeamDialog = ref(false)
const teamName = ref('')
const teamNameError = ref('')
const teamHandle = ref('')
const teamHandleError = ref('')
const addressPrefix = `${window.location.host}/teams/`
const teamDescription = ref<JSONContent>({ type: 'doc', content: [] })
const teamDescriptionEditor = ref<InstanceType<typeof TipTapEditor>>()
const teamAvatar = ref<File | undefined>()
const creatingTeam = ref(false)

const router = useRouter()

const openCreateTeamDialog = () => {
  createTeamDialog.value = true
}

const createTeam = async () => {
  if (!teamName.value) {
    toast.error(t('teams.index.nameRequired'))
    return
  }

  teamNameError.value = ''
  teamHandleError.value = ''
  try {
    creatingTeam.value = true
    let intro = teamDescriptionEditor.value?.editor?.getText() ?? ''
    if (intro.length > 250) {
      intro = `${intro.slice(0, 250)}…`
    }

    const {
      data: { avatarId },
    } = teamAvatar.value ? await AvatarsApi.createAvatar(teamAvatar.value) : { data: { avatarId: null } }

    const {
      data: { team },
    } = await TeamsApi.create({
      name: teamName.value,
      description: JSON.stringify(teamDescription.value),
      intro,
      avatarId: avatarId ?? 1,
      handle: teamHandle.value.trim() || undefined,
    })

    toast.success(t('teams.index.createDone'))
    router.push({ name: 'TeamsDetailDefault', params: { handle: team.handle } })
  } catch (error) {
    if (error instanceof BusinessError && error.error?.data?.field === 'handle') {
      teamHandleError.value = error.code === 409 ? t('work.teamLink.handleTaken') : t('work.teamLink.handleInvalid')
      return
    }
    // 团队名全站唯一，撞名是 409 + data.field=name：用户换个名字就能解决，
    // 所以落到名字那一格，不能混进「稍后重试」—— 重试多少次都一样失败。
    if (error instanceof BusinessError && error.code === 409 && error.error?.data?.field === 'name') {
      teamNameError.value = t('work.teamProfile.nameTaken')
      return
    }
    toast.error(t('teams.index.createFailed'))
    console.error(error)
  } finally {
    creatingTeam.value = false
  }
}
</script>

<style scoped></style>
