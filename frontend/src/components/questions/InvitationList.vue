<template>
  <v-list>
    <v-list-item v-for="(user, index) in data" :key="user.id" :title="user.nickname" :subtitle="user.intro">
      <template #prepend>
        <!-- 种子用 handle(username)：颜色跟着人走，不跟昵称走，改昵称不换色（契约 §3.14）。 -->
        <user-avatar :avatar="getAvatarUrl(user.avatarId)" :name="user.nickname" :seed="user.username" />
      </template>
      <template #append>
        <BaseButton
          :kind="isInvited[index] ? 'ghost' : 'secondary'"
          :disabled="isInvited[index]"
          @click="invite(index)"
        >
          <v-icon class="me-2">mdi-account-multiple-plus</v-icon>
          {{
            isInvited[index]
              ? t('questions.invitationList.buttons.invited')
              : t('questions.invitationList.buttons.invite')
          }}
        </BaseButton>
      </template>
    </v-list-item>
  </v-list>
</template>

<script setup lang="ts">
import type { User } from '@/types'

import { computed, ref, toRefs, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { getErrorMessage } from '@/utils/errors'
import { getAvatarUrl } from '@/utils/materials'

import UserAvatar from '../common/UserAvatar.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { QuestionApi } from '@/network/api/questions'
import AccountService from '@/services/account'

const { t } = useI18n()

const props = defineProps<{
  questionId: number
}>()

const { questionId } = toRefs(props)

const data = ref<User[]>([])
const invitedUsers = ref<User[]>([])

const isInvited = computed(() => {
  return data.value.map((user) => invitedUsers.value.some((invitedUser) => invitedUser.id === user.id))
})

/** 登没登录。读的是顶栏那几个组件读的同一份状态（AppBar / HelpAndFeedbackMenu
 *  都是 `AccountService._loggedIn.value`）。
 *
 *  **不用 `currentUserId`**：那个跟着 `user` 对象走，而 `user` 要等一趟
 *  `updateUserInfo()` 回来才落地 —— 拿它当闸门，会把「已登录、档案还没回来」的人
 *  当成游客，而这两条读侧后端明明会给他 200。`_loggedIn` 才是「登没登录」的答案。 */
const loggedIn = computed(() => AccountService._loggedIn.value)

const invite = async (index: number) => {
  const user = data.value[index]
  if (user && !isInvited.value[index]) {
    try {
      const { data } = await QuestionApi.inviteUser(questionId.value, user.id)
      if (data) {
        invitedUsers.value.push(user)
      }
    } catch (e) {
      toast.error(t('questions.invitationList.errors.inviteFailed', { reason: getErrorMessage(e) }))
    }
  }
}

/** 拉「可以邀请谁」与「已经邀请了谁」。
 *
 *  **只有登录了才发这两趟请求**：后端这两条读侧要登录，匿名会拿到 401。游客只是
 *  还不能邀请人 —— 替他打一趟必然 401 的请求、再把 401 翻成一句报错弹窗，是拿他
 *  看不懂的东西拦他。所以这里既不请求，也不报错，只是留一张空名单。
 *
 *  **watch 而不是 onMounted**：这个组件住在对话框里，人也可能先到页面、后登录；
 *  只在挂载那一刻问一次的话，登录后这份名单就永远是空的（顶栏那两个组件踩过同一
 *  脚，见 HelpAndFeedbackMenu.vue 的 refresh）。
 *
 *  失败给一句 toast，不静默：这个名单就是组件的全部正文，静默失败会把它画成「没有
 *  人可以邀请」——那是另一件事（spaces/detail 的邀请码列表按同一理由不许失败被当成
 *  空板）。 */
async function load() {
  if (!loggedIn.value || !questionId.value) return

  try {
    const [
      {
        data: { users },
      },
      {
        data: { invitations },
      },
    ] = await Promise.all([
      QuestionApi.invitationRecommend(questionId.value),
      QuestionApi.getInvitaions(questionId.value),
    ])
    data.value = users
  } catch {
    toast.error(t('questions.invitationList.errors.loadFailed'))
  }
}

watch(loggedIn, load, { immediate: true })
</script>
