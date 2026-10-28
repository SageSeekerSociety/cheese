// 首页目录的名单：我所在的团队、我加入的空间。桌面侧栏（`HomeSidebar`）和手机首页
// （`HomeHub`）读的是同一份，从空间或项目回到首页时侧栏会卸载重挂，名单留在缓存里，
// 第一帧就画得出来，不用从空开始等请求。
import type { SpaceLink, Team } from '@/types'

import { queryOptions } from '@tanstack/vue-query'

import { SpacesApi } from '@/api/spaces'
import { TeamsApi } from '@/api/teams'
import { keys } from '@/query/keys'

/** 我所在的团队（含自己名下那一个）。 */
export function myTeamsQuery() {
  return queryOptions({
    queryKey: keys.myTeams(),
    queryFn: async (): Promise<Team[]> => (await TeamsApi.getMyTeams()).teams,
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
    queryFn: async (): Promise<SpaceLink[]> => {
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
    },
  })
}
