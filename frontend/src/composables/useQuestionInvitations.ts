// 「邀请谁来回答」那份名单：可以邀请谁、已经邀请了谁、发一封邀请，以及「登没登录」
// 这个闸门。页面（`views/question/Detail.vue`）拿它，把名单和「已经邀请过谁」交给
// `components/questions/InvitationList.vue` 画 —— 那个组件不吃 API 层
// （.claude/rules/architecture.md）。
import type { MaybeRefOrGetter } from 'vue'
import type { User } from '@/types'

import { computed, ref, toValue, watch } from 'vue'

import { getErrorMessage } from '@/utils/errors'

import { QuestionApi } from '@/api/questions'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'
import AccountService from '@/services/account'

export function useQuestionInvitations(
  questionId: MaybeRefOrGetter<number>,
  /** `active` 是「这会儿有人看得见这份名单吗」。页面把它接到邀请弹窗的开合上：弹窗
   *  没开就不发那两趟请求（弹窗里的名单是懒挂载的，不开就没人看得见）。默认一直读。 */
  options: { active?: MaybeRefOrGetter<boolean> } = {}
) {
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

  const active = computed(() => (options.active === undefined ? true : Boolean(toValue(options.active))))

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
   *  空板）。
   *
   *  **每一趟都认题**：这一份实例跟着页面一直活着，页面换一道题时它不重建，`questionId`
   *  只是换了个值。所以开读之前先记下这趟是给哪道题读的，回来对不上就丢掉；开读的
   *  那一刻也先清空旧名单。少了这两样，新题的弹窗会先画出上一题的人，点到哪一个都
   *  会邀请到新题上。 */
  async function load(): Promise<void> {
    const id = toValue(questionId)
    if (!active.value || !loggedIn.value || !id) return

    // 清在发请求之前，不是回来之后：晚清一步，那一步里画的就是上一题的人。
    people.value = []
    invited.value = []

    try {
      const [{ users }, { invitations }] = await Promise.all([
        QuestionApi.invitationRecommend(id),
        QuestionApi.getInvitaions(id),
      ])
      if (toValue(questionId) !== id) return
      people.value = users
      // 后端在档案缺失时会把这条邀请的 `user` 给成 null（`_profile_to_user` 没有
      // 档案就返回 None），而前端的 `QuestionInvitation` 把它写成了必有——按它写的
      // 信会在这里拿到 null 再 `.id`。滤掉。
      invited.value = invitations.map((invitation) => invitation.user).filter(Boolean)
    } catch {
      // 已经换了题就不报：这句「名单拉不到」说的是新题，而这趟是旧题的。
      if (toValue(questionId) !== id) return
      toast.error(t('questions.invitationList.errors.loadFailed'))
    }
  }

  /** 发一封邀请。成了就把这个人记进「已邀请」，那颗按钮当场变成「已邀请」。
   *  这一趟同样认题：发出去之后人又换了题的话，这个人不属于新题那份名单。 */
  async function invite(user: User): Promise<void> {
    if (isInvited(user)) return
    const id = toValue(questionId)
    try {
      const data = await QuestionApi.inviteUser(id, user.id)
      if (data && toValue(questionId) === id) invited.value.push(user)
    } catch (e) {
      toast.error(t('questions.invitationList.errors.inviteFailed', { reason: getErrorMessage(e) }))
    }
  }

  // 名单重新读的时机：人登录了、名单重新变得看得见（弹窗又开了一次）、或者换了一道题
  // （弹窗开着的换题，名单得换成新题那份）。关掉的那一下不发请求——那一趟刚读回来，
  // 没人看的时候再读一遍是白花钱。
  watch([loggedIn, active, questionId], load, { immediate: true })

  return { people, invited, isInvited, loggedIn, invite }
}
