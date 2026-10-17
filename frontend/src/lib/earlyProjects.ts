// 启动脚本（index.html）在应用代码还在下载时就替它问了一次项目清单：这里把那次的答复
// 交给应用的第一次读取（`query/projects.ts`）。只用一次，之后都是应用自己问。
import type { ListPayload, Project } from '@/cx_types'

import { authToken } from '@/api/http'

interface EarlyAnswer {
  token: string
  body: unknown
}

declare global {
  interface Window {
    __cxEarlyProjects?: Promise<EarlyAnswer | null>
  }
}

/** 启动脚本问到的项目清单。没问、没问到，或者问的时候用的令牌已经不是现在这个（这期间
 *  换了人、退出了），都是 null：调用方自己再问一次。 */
export async function takeEarlyProjects(): Promise<ListPayload<Project> | null> {
  const early = window.__cxEarlyProjects
  if (!early) return null
  delete window.__cxEarlyProjects
  const answer = await early.catch(() => null)
  if (!answer || answer.token !== authToken()) return null
  const body = answer.body as { code?: unknown; data?: { data?: unknown } } | null
  return body?.code === 200 && Array.isArray(body.data?.data) ? (body.data as ListPayload<Project>) : null
}
