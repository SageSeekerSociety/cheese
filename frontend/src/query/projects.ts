// 我能看到的项目：左边栏的项目格子、工作区的项目名、登录后落在哪个项目，读的都是这一份。
import type { Project } from '@/cx_types'

import { queryOptions } from '@tanstack/vue-query'

import { listProjects } from '@/api'
import { rememberProjects } from '@/lib/addresses'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

export function projectsQuery() {
  return queryOptions({
    queryKey: keys.projects(),
    queryFn: async (): Promise<Project[]> => {
      const list = (await listProjects()).data
      rememberProjects(list)
      return list
    },
  })
}

/** 项目清单变了（新建、归档、转交、退出）：再读一次。 */
export function refreshProjects(): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: keys.projects() })
}
