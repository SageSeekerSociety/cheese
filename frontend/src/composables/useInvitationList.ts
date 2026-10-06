// 「邀请回答」弹窗里那份名单：可以邀请谁、已经邀请了谁、把谁请上。
//
// 画面那一半 `components/questions/InvitationListView.vue` 只认 props、只发事件。
// 取数原来长在 `InvitationList.vue` 里，现在收在这里，让那半取数的组件与问题详情
// 容器共用同一份实现（详情页的邀请弹窗由容器自己取数、把名单递给视图）。
import type { User } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'

import { getErrorMessage } from '@/utils/errors'

import { QuestionApi } from '@/network/api/questions'
import AccountService from '@/services/account'

export function useInvitationList(questionId: () => number) {
  const { t } = useI18n()

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

  async function invite(index: number) {
    const user = data.value[index]
    if (user && !isInvited.value[index]) {
      try {
        const { data: res } = await QuestionApi.inviteUser(questionId(), user.id)
        if (res) {
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
    if (!loggedIn.value || !questionId()) return

    try {
      const [
        {
          data: { users },
        },
      ] = await Promise.all([QuestionApi.invitationRecommend(questionId()), QuestionApi.getInvitaions(questionId())])
      data.value = users
    } catch {
      toast.error(t('questions.invitationList.errors.loadFailed'))
    }
  }

  watch(loggedIn, load, { immediate: true })

  return { users: data, invitedUsers, isInvited, invite }
}
