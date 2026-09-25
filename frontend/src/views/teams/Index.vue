<template>
  <PageHeader icon="mdi-account-group" title="团队">
    <template #tabs>
      <v-tabs slider-color="primary" bg-color="transparent">
        <v-tab :to="{ name: 'HomeTeamsExplore' }">发现</v-tab>
        <v-tab :to="{ name: 'HomeTeamsMine' }">我的</v-tab>
        <v-tab :to="{ name: 'HomeTeamsPending' }">待定</v-tab>
      </v-tabs>
      <v-btn
        color="primary"
        variant="tonal"
        rounded="md"
        size="small"
        prepend-icon="mdi-plus"
        @click="openCreateTeamDialog"
      >
        创建团队
      </v-btn>
    </template>
  </PageHeader>

  <router-view />

  <!-- 创建小队对话框 -->
  <v-dialog v-model="createTeamDialog" width="600">
    <v-card rounded="lg" class="create-team-dialog elevation-0 border">
      <v-toolbar color="transparent" flat>
        <v-toolbar-title class="text-h6">创建团队</v-toolbar-title>
        <v-spacer></v-spacer>
        <v-btn icon @click="createTeamDialog = false">
          <v-icon>mdi-close</v-icon>
        </v-btn>
      </v-toolbar>

      <v-divider></v-divider>

      <v-card-text class="py-5">
        <v-form>
          <v-container fluid>
            <v-row>
              <v-col cols="12" md="4" class="text-center">
                <avatar-uploader v-model="teamAvatar" />
                <p class="text-body-2 text-medium-emphasis mb-2">团队头像</p>
              </v-col>
              <v-col cols="12" md="8">
                <v-text-field
                  v-model="teamName"
                  autocomplete="off"
                  label="团队名称"
                  variant="outlined"
                  color="primary"
                  placeholder="输入团队名称..."
                  :rules="[(v) => !!v || '请输入团队名称']"
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

                <p class="text-body-2 text-medium-emphasis mb-2">团队描述</p>
                <tip-tap-editor
                  ref="teamDescriptionEditor"
                  v-model="teamDescription"
                  output="html"
                  placeholder="描述你的团队定位、目标和文化..."
                  class="team-description-editor rounded-md"
                />
              </v-col>
            </v-row>
          </v-container>
        </v-form>
      </v-card-text>

      <v-divider></v-divider>

      <v-card-actions class="pa-4">
        <v-spacer></v-spacer>
        <v-btn variant="text" class="mr-2" @click="createTeamDialog = false">取消</v-btn>
        <v-btn color="primary" variant="elevated" rounded="md" :loading="creatingTeam" @click="createTeam">
          创建团队
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { JSONContent } from 'vuetify-pro-tiptap'

import { defineAsyncComponent, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import AvatarUploader from '@/components/common/AvatarUploader.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

const createTeamDialog = ref(false)
const teamName = ref('')
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
    toast.error('请输入团队名称')
    return
  }

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

    toast.success('创建团队成功')
    router.push({ name: 'TeamsDetailDefault', params: { handle: team.handle } })
  } catch (error) {
    if (error instanceof BusinessError && error.error?.data?.field === 'handle') {
      teamHandleError.value = error.code === 409 ? t('work.teamLink.handleTaken') : t('work.teamLink.handleInvalid')
      return
    }
    toast.error('创建团队失败，请稍后重试')
    console.error(error)
  } finally {
    creatingTeam.value = false
  }
}
</script>

<style scoped></style>
