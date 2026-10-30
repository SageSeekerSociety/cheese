// 个人主页取数：一次读三份（主页、项目成员名册里那一行、最近参与的话题），三份一起
// 回来才算这一页读完。回到看过的主页先照着上一次的画，背后再取（query/client）；
// 保活着离开时停下、回来时过期了的再读（usePageActive）。
import type { MemberSummary, ProfileTopic, ProjectAgent, UserProfile } from '@/cx_types'

import { computed, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'

import { getAvatarUrl } from '@/utils/materials'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { usePageActive } from '@/composables/usePageActive'
import { usePageTitle } from '@/composables/usePageTitle'
import { holdRevealUntil } from '@/composables/useRevealGate'

import { getMemberSummary, getUserProfile, getUserTopics, setUserRemark } from '@/api'
import i18n, { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { agentsQuery } from '@/query/project'
import { fromSnapshot } from '@/query/snapshot'

/** 「最近参与的话题」列几个。 */
export const RECENT_TOPICS = 8

export interface ProfilePage {
  profile: UserProfile
  member: MemberSummary | null
  /** 最近参与的话题；这一段单独失败时是 null，页面其余部分照常。 */
  recent: ProfileTopic[] | null
  /** 这个项目里他名下的 AI 队友；「带的技能」从这儿取。取不到就是空的。 */
  agents: ProjectAgent[]
}

/**
 * @param handle 这一页是谁。
 * @param projectId 从项目成员名册进来的那个入口给项目，站内那个入口不给。
 */
export function useProfilePage(handle: () => string, projectId: () => string | undefined) {
  ensureDefaultAvatarId()

  // 回到看过的主页先照着上一次的画，背后重取（query/client）。
  const pageKey = computed(() => ['user', handle(), 'profile', projectId() ?? null] as const)
  // 保活着离开时停下，回来时过期了的再读（usePageActive）。
  const pageActive = usePageActive()
  const page = useQuery(
    computed(() => ({
      enabled: pageActive.value,
      queryKey: pageKey.value,
      queryFn: async (): Promise<ProfilePage> => {
        const target = handle()
        const [profile, member, recent, agents] = await Promise.all([
          fromSnapshot(keys.userProfile(target), () => getUserProfile(target)),
          projectId() ? getMemberSummary(projectId()!, target).catch(() => null) : Promise.resolve(null),
          fromSnapshot(keys.userRecentTopics(target), () => getUserTopics(target, { limit: RECENT_TOPICS }))
            .then((r) => r.topics)
            .catch(() => null),
          projectId() ? queryClient.fetchQuery(agentsQuery(projectId()!)).catch(() => []) : Promise.resolve([]),
        ])
        return { profile, member, recent, agents }
      },
    })),
    queryClient
  )
  holdRevealUntil(() => !page.isPending.value)
  const data = page.data
  // 只在没有任何内容可画时转圈、报错；有内容时背后那次没取到就照常显示。
  const loading = page.isPending
  const error = computed(() => (page.data.value ? null : page.error.value))
  function refresh() {
    void page.refetch()
  }

  const profile = computed(() => data.value?.profile ?? null)
  const member = computed(() => data.value?.member ?? null)
  // 平台上没有这个 handle 的账号：后端照样回一份空的主页，加入时间是空的。
  const missing = computed(() => profile.value !== null && profile.value.joined_at === null)
  const remark = computed(() => profile.value?.remark?.trim() || '')
  const originalName = computed(() => profile.value?.name || '')
  const displayName = computed(() => remark.value || originalName.value || handle())

  async function saveRemark(nextRemark: string): Promise<string | null> {
    const res = await setUserRemark(handle(), nextRemark)
    const current = data.value
    if (current) {
      queryClient.setQueryData<ProfilePage>(pageKey.value, {
        ...current,
        profile: { ...current.profile, remark: res.remark },
      })
    }
    void queryClient.invalidateQueries({ queryKey: ['project'] })
    void queryClient.invalidateQueries({ queryKey: ['room'] })
    return res.remark
  }

  const avatarUrl = computed(() => {
    const id = profile.value?.avatar_id
    return isChosenAvatar(id) ? getAvatarUrl(id!) : ''
  })

  const locale = computed(() => i18n.global.locale.value)
  const joined = computed(() => {
    const at = profile.value?.joined_at
    if (!at) return ''
    const date = new Intl.DateTimeFormat(locale.value, { year: 'numeric', month: 'long' }).format(new Date(at))
    return t('users.profile.joined', { date })
  })

  // 顶栏（手机）和标签页标题写这个人的名字，而不是路由上那个泛泛的「成员」。
  const { setDynamicTitle } = usePageTitle()
  watch(
    () => (profile.value ? displayName.value : ''),
    (name) => {
      if (name) setDynamicTitle(name, projectId() ? 'member' : 'UserPage')
    },
    { immediate: true }
  )

  return {
    data,
    pageKey,
    loading,
    error,
    refresh,
    profile,
    member,
    missing,
    remark,
    originalName,
    displayName,
    saveRemark,
    avatarUrl,
    locale,
    joined,
  }
}
