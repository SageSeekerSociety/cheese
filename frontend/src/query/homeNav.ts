// 首页目录的名单：我所在的团队、我加入的空间。桌面侧栏（`HomeSidebar`）和手机首页
// （`HomeHub`）读的是同一份，从空间或项目回到首页时侧栏会卸载重挂，名单留在缓存里，
// 第一帧就画得出来，不用从空开始等请求。
import type { SpaceLink, Team } from '@/types'

import { queryOptions } from '@tanstack/vue-query'

import { getUserProfile, getUserTopics, listAwaitingMe, listTeamDevices } from '@/api'
import { getNotificationPreferences } from '@/api/notificationPreferences'
import { NotificationsApi } from '@/api/notifications'
import { SpacesApi } from '@/api/spaces'
import { TeamsApi } from '@/api/teams'
import { storageGet } from '@/lib/browserStorage'
import { myHandle } from '@/me'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { fromSnapshot, readEarly } from '@/query/snapshot'

async function fetchMyTeams(): Promise<Team[]> {
  return (await TeamsApi.getMyTeams()).teams
}

async function fetchMySpaces(): Promise<SpaceLink[]> {
  const [listed, applied] = await Promise.all([
    SpacesApi.list({ pageSize: 50, sort_by: 'created_at', sort_order: 'desc' }),
    SpacesApi.applications().catch(() => ({ items: [] })),
  ])
  const next: SpaceLink[] = listed.spaces.map((space) => ({
    id: space.id,
    name: space.name,
    avatarId: space.avatarId,
  }))
  for (const item of applied.items) {
    if (item.reviewStatus === 'APPROVED' || next.some((space) => space.id === item.id)) continue
    next.push({ id: item.id, name: item.name, avatarId: item.avatarId, review: item.reviewStatus })
  }
  return next
}

/** 我所在的团队（含自己名下那一个）。 */
export function myTeamsQuery() {
  return queryOptions({
    queryKey: keys.myTeams(),
    queryFn: (): Promise<Team[]> => fromSnapshot(keys.myTeams(), fetchMyTeams),
  })
}

/**
 * 空间那一组 = 我加入的空间，加上我建的、还没过审的那几个（待审核或被驳回）。
 *
 * 后者不在空间列表里（没过审的空间谁的列表里都没有），只能从「我的申请」里读；过审的
 * 申请就是一个普通空间，已经在前一份里了。申请读不到不该连累已加入的空间：退回一份
 * 空的。
 */
export function mySpacesQuery() {
  return queryOptions({
    queryKey: keys.mySpaces(),
    queryFn: (): Promise<SpaceLink[]> => fromSnapshot(keys.mySpaces(), fetchMySpaces),
  })
}

/**
 * 待办、个人主页、个人设置（设备/通知）的首屏数据，趁路由代码块还在下载时提前出发。
 */
export function prefetchPersonalRoute(
  routeName: string | symbol | null | undefined,
  params: Record<string, unknown>
): void {
  if (typeof routeName !== 'string') return
  switch (routeName) {
    case 'inbox': {
      readEarly(keys.awaitingMe(), () => listAwaitingMe())
      const unreadOnly = storageGet('cheesex.notificationFeed.unreadOnly') === '1'
      readEarly(keys.inboxNotifications(unreadOnly), () =>
        NotificationsApi.list({
          read: unreadOnly ? false : undefined,
          pageStart: undefined,
          pageSize: 20,
        })
      )
      readEarly(keys.myTeams(), fetchMyTeams)
      readEarly(keys.mySpaces(), fetchMySpaces)
      break
    }
    case 'UserPage':
    case 'member': {
      const handle = typeof params.handle === 'string' ? params.handle : ''
      if (!handle) break
      const projectId = typeof params.projectId === 'string' ? params.projectId : null
      const state = queryClient.getQueryState(['user', handle, 'profile', projectId])
      if (state?.status === 'success' && Date.now() - state.dataUpdatedAt < 30_000) break
      readEarly(keys.userProfile(handle), () => getUserProfile(handle))
      readEarly(keys.userRecentTopics(handle), () => getUserTopics(handle, { limit: 8 }))
      break
    }
    case 'UserSettingsCompute':
    case 'UserSettingsModels':
    case 'UserSettingsCredits': {
      const handle = myHandle()
      if (!handle) break
      const cached = queryClient
        .getQueryData<Team[]>(keys.myTeams())
        ?.find((team) => team.handle.toLowerCase() === handle.toLowerCase())
      if (cached && routeName === 'UserSettingsCompute') {
        readEarly(keys.teamDevices(cached.id), () => listTeamDevices(cached.id))
      }
      readEarly(keys.ownTeam(handle), async () => {
        const res = await TeamsApi.detailByHandle(handle)
        if (!cached && routeName === 'UserSettingsCompute' && res.team?.id) {
          readEarly(keys.teamDevices(res.team.id), () => listTeamDevices(res.team.id))
        }
        return res
      })
      break
    }
    case 'UserSettingsNotifications':
      readEarly(keys.notificationPreferences(), () => getNotificationPreferences())
      break
  }
}
