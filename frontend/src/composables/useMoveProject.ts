// 「移到团队」（#3830）：所有者把自己的个人项目移到自己管理的团队。读得到哪些团队、移过去
// 之后刷新项目清单，都在这一层，组件不直接碰 API 层。
import type { MaybeRefOrGetter } from 'vue'

import { ref, toValue } from 'vue'

import { moveProjectToTeam } from '@/api'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { projectQuery } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'

export interface MovableTeam {
  id: number
  name: string
}

export function useMoveProject(projectId: MaybeRefOrGetter<string>) {
  const store = useWorkspaceStore()
  const teams = ref<MovableTeam[]>([])
  const loading = ref(false)
  const moving = ref(false)
  const error = ref<string | null>(null)

  /** 我管理的团队（所有者或管理员），个人名下那一个除外。 */
  async function load() {
    loading.value = true
    error.value = null
    try {
      const { teams: mine } = await TeamsApi.getMyTeams()
      teams.value = mine
        .filter((team) => !team.personal && (team.role === 'OWNER' || team.role === 'ADMIN'))
        .map((team) => ({ id: team.id, name: team.name }))
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.members.moveFailed')
    } finally {
      loading.value = false
    }
  }

  async function move(teamId: number): Promise<boolean> {
    moving.value = true
    error.value = null
    try {
      await moveProjectToTeam(toValue(projectId), teamId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.members.moveFailed')
      moving.value = false
      return false
    }
    void Promise.allSettled([
      store.refreshProjects(),
      queryClient.invalidateQueries({ queryKey: projectQuery(toValue(projectId)).queryKey }),
    ]).finally(() => {
      moving.value = false
    })
    return true
  }

  return { teams, loading, moving, error, load, move }
}
