<template>
  <IndexView
    :address-prefix="addressPrefix"
    :name-error="teamNameError"
    :handle-error="teamHandleError"
    :creating="creatingTeam"
    @create="createTeam"
  />
</template>

<script setup lang="ts">
// 「团队」页（`/teams`）的**容器**：建队要的那几个接口（头像、建队、跳过去）与接口
// 回的错都在这儿；画的那一半在 `IndexView.vue`。表单自己那几格（名字、地址、介绍、
// 头像）归视图，提交时把这一份原样交上来。
import type { JSONContent } from '@tiptap/core'

import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import IndexView from './IndexView.vue'

import { t } from '@/i18n'
import { AvatarsApi } from '@/network/api/avatars'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

/** 视图提交上来的那一份表单：接口要什么它就有什么。 */
interface CreateTeamForm {
  name: string
  handle: string
  description: JSONContent
  avatar: File | undefined
  intro: string
}

const router = useRouter()

const teamNameError = ref('')
const teamHandleError = ref('')
const addressPrefix = `${window.location.host}/teams/`
const creatingTeam = ref(false)

const createTeam = async (form: CreateTeamForm) => {
  if (!form.name) {
    toast.error(t('teams.index.nameRequired'))
    return
  }

  teamNameError.value = ''
  teamHandleError.value = ''
  try {
    creatingTeam.value = true

    const {
      data: { avatarId },
    } = form.avatar ? await AvatarsApi.createAvatar(form.avatar) : { data: { avatarId: null } }

    const {
      data: { team },
    } = await TeamsApi.create({
      name: form.name,
      description: JSON.stringify(form.description),
      intro: form.intro,
      avatarId: avatarId ?? 1,
      handle: form.handle.trim() || undefined,
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
