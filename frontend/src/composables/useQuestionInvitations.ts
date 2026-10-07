// 「邀请谁来回答」那份名单：可以邀请谁、已经邀请了谁、发一封邀请，以及「登没登录」
// 这个闸门。`components/questions/InvitationList.vue` 只画这些人、点那颗按钮——组件
// 不吃 API 层（.claude/rules/architecture.md）。
import type { MaybeRefOrGetter } from 'vue'
import type { User } from '@/types'

import { computed, ref, toValue, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { getErrorMessage } from '@/utils/errors'

import { t } from '@/i18n'
import { QuestionApi } from '@/network/api/questions'
import AccountService from '@/services/account'

export function useQuestionInvitations(questionId: MaybeRefOrGetter<number>) {
  /** 推荐名单：可以邀请谁。 */
  const people = ref<User[]>([])
  /** 已经邀请了谁。推荐接口不过滤被邀请过的人（后端 `get_recommendations` 就是
   *  一份全量档案列表），所以「这颗按钮是不是已经点过」只能靠这一份比对。 */
  const invited = ref<User[]>([])

  /** 登没登录。读的是顶栏那几个组件读的同一份状态（AppBar / HelpAndFeedbackMenu
   *  都是 `AccountService._loggedIn.value`）。
   *
   *  **不用 `currentUserId`**：那个跟着 `user` 对象走，而 `user` 要等一趟
   *  `updateUserInfo()` 回来才落地 —— 拿它当闸门，会把「已登录、档案还没回来」的人
   *  当成游客，而这两条读侧后端明明会给他 200。`_loggedIn` 才是「登没登录」的答案。 */
  const loggedIn = computed(() => AccountService._loggedIn.value)

  const isInvited = (user: User): boolean => invited.value.some((row) => row.id === user.id)

  /** 拉「可以邀请谁」与「已经邀请了谁」。
   *
   *  **只有登录了才发这两趟请求**：后端这两条读侧要登录，匿名会拿到 401。游客只是
   *  还不能邀请人 —— 替他打一趟必然 401 的请求、再把 401 翻成一句报错弹窗，是拿他
   *  看不懂的东西拦他。所以这里既不请求，也不报错，只是留一张空名单。
   *
   *  **watch 而不是 onMounted**：这个名单住在对话框里，人也可能先到页面、后登录；
   *  只在挂载那一刻问一次的话，登录后这份名单就永远是空的（顶栏那两个组件踩过同一
   *  脚，见 HelpAndFeedbackMenu.vue 的 refresh）。
   *
   *  失败给一句 toast，不静默：这个名单就是那段正文，静默失败会把它画成「没有
   *  人可以邀请」——那是另一件事（spaces/detail 的邀请码列表按同一理由不许失败被当成
   *  空板）。 */
  async function load(): Promise<void> {
    if (!loggedIn.value || !toValue(questionId)) return

    try {
      const [
        {
          data: { users },
        },
        {
          data: { invitations },
        },
      ] = await Promise.all([
        QuestionApi.invitationRecommend(toValue(questionId)),
        QuestionApi.getInvitaions(toValue(questionId)),
      ])
      people.value = users
      // 后端在档案缺失时会把这条邀请的 `user` 给成 null（`_profile_to_user` 没有
      // 档案就返回 None），而前端的 `QuestionInvitation` 把它写成了必有——按它写的
      // 信会在这里拿到 null 再 `.id`。滤掉。
      invited.value = invitations.map((invitation) => invitation.user).filter(Boolean)
    } catch {
      toast.error(t('questions.invitationList.errors.loadFailed'))
    }
  }

  /** 发一封邀请。成了就把这个人记进「已邀请」，那颗按钮当场变成「已邀请」。 */
  async function invite(user: User): Promise<void> {
    if (isInvited(user)) return
    try {
      const { data } = await QuestionApi.inviteUser(toValue(questionId), user.id)
      if (data) invited.value.push(user)
    } catch (e) {
      toast.error(t('questions.invitationList.errors.inviteFailed', { reason: getErrorMessage(e) }))
    }
  }

  watch(loggedIn, load, { immediate: true })

  return { people, isInvited, loggedIn, invite }
}
