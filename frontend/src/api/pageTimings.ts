import { request } from './http'

export interface RequestTiming {
  path: string
  start: number
  duration: number
  ttfb: number | null
  bytes: number
}

export interface PageTiming {
  route: string
  host: string
  first_load: boolean
  loaded: number | null
  requests: RequestTiming[]
}

/** 这一页在读者自己的浏览器里花了多久（后端 `routes/page_timings.py`）。 */
export function reportPageTiming(projectId: string, timing: PageTiming): Promise<unknown> {
  return request(`/projects/${encodeURIComponent(projectId)}/page-timings`, {
    method: 'POST',
    body: JSON.stringify(timing),
  })
}
