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
import errorHandler from '@/services/ErrorHandler'

const asError = (e: unknown) => (e instanceof Error ? e : new Error(String(e)))

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

/** 一次没建成的失败，只有一句：服务端有条有理回绝的（带着业务 code）就说它自己那句
 *  ——「这个空间名称已被占用」这类，人照着能改；其余的（网络断了、未知错误）才说
 *  `failed` 那一句「稍后重试」。两句一起说时后一句只是把前一句盖上一层。 */
const explain = async (error: unknown, failed: string) => {
  toast.error(await errorHandler.explain(asError(error), failed))
}

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

    // 上传头像和建团队分开说：举不上去和存不下是两件事，人要做的事也不一样。
    let avatarId: number | null = null
    if (teamAvatar.value) {
      try {
        avatarId = (await createAvatar(teamAvatar.value)).avatarId
      } catch (error) {
        await explain(error, t('teams.index.avatarFailed'))
        return
      }
    }

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
    // 服务端有条有理回绝的（带着业务 code）就说它自己那句——「内容安全审核暂时不可用」
    // 这类，人照着能改；其余的（网络断了、未知错误）才用通用文案。
    await explain(error, t('teams.index.createFailed'))
    console.error(error)
  } finally {
    creatingTeam.value = false
  }
}
</script>
