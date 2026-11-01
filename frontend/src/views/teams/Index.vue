<template>
  <IndexView
    v-model:create-team-dialog="createTeamDialog"
    v-model:team-name="teamName"
    v-model:team-handle="teamHandle"
    v-model:team-description="teamDescription"
    v-model:team-avatar="teamAvatar"
    :creating-team="creatingTeam"
    :team-name-error="teamNameError"
    :team-handle-error="teamHandleError"
    :address-prefix="addressPrefix"
    @create-team="createTeam"
  >
    <router-view />
  </IndexView>
</template>

<script setup lang="ts">
// 「团队」这一栏的外框：页头、三个标签和「创建团队」弹窗。创建要发请求、成功要跳进
// 新建的那个团队，那两件事留在这里；画面在 `IndexView.vue`，只收状态、把「要创建」
// 连同它取到的简介交上来。
import type { JSONContent } from '@tiptap/core'

import { ref } from 'vue'
import { useRouter } from 'vue-router'

import IndexView from './IndexView.vue'

import { ApiError } from '@/api'
import { createAvatar } from '@/api/avatars'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

const createTeamDialog = ref(false)
const teamName = ref('')
const teamNameError = ref('')
const teamHandle = ref('')
const teamHandleError = ref('')
const addressPrefix = `${window.location.host}/teams/`
const teamDescription = ref<JSONContent>({ type: 'doc', content: [] })
const teamAvatar = ref<File | undefined>()
const creatingTeam = ref(false)

const router = useRouter()

/** 简介那一句由画面从编辑器正文里取好递过来（编辑器住在画面那一半）。 */
const createTeam = async (intro: string) => {
  if (!teamName.value) {
    toast.error(t('teams.index.nameRequired'))
    return
  }

  teamNameError.value = ''
  teamHandleError.value = ''
  try {
    creatingTeam.value = true

    const { avatarId } = teamAvatar.value ? await createAvatar(teamAvatar.value) : { avatarId: null }

    const { team } = await TeamsApi.create({
      name: teamName.value,
      description: JSON.stringify(teamDescription.value),
      intro,
      avatarId: avatarId ?? 1,
      handle: teamHandle.value.trim() || undefined,
    })

    toast.success(t('teams.index.createDone'))
    router.push({ name: 'TeamsDetailDefault', params: { handle: team.handle } })
  } catch (error) {
    const field = error instanceof ApiError ? (error.data as { field?: string } | undefined)?.field : undefined
    if (error instanceof ApiError && field === 'handle') {
      teamHandleError.value = error.status === 409 ? t('work.teamLink.handleTaken') : t('work.teamLink.handleInvalid')
      return
    }
    // 团队名全站唯一，撞名是 409 + data.field=name：用户换个名字就能解决，
    // 所以落到名字那一格，不能混进「稍后重试」—— 重试多少次都一样失败。
    if (error instanceof ApiError && error.status === 409 && field === 'name') {
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
