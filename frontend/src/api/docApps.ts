// 文档里的小程序（`:::app <短号>@<版本>`）：它有哪些版本、某一版的代码和外壳地址、
// 交稿时平台拍的截图。读得了它所属那份文档的人才拿得到，不论块出现在哪里。
import type { DocApp, DocAppVersion } from '../lib/docApps'

import { t } from '../i18n'

import { ApiError, authHeaders, BASE, request } from './http'

const app = (key: string) => `/apps/${encodeURIComponent(key)}`

export function getDocApp(key: string): Promise<DocApp> {
  return request<DocApp>(app(key))
}

export function getDocAppVersion(key: string, version: number): Promise<DocAppVersion> {
  return request<DocAppVersion>(`${app(key)}/versions/${version}`)
}

/** 某一版在某个宽度、某种深浅下的截图，取回来做成 object URL；用完 `URL.revokeObjectURL`。 */
export async function docAppShotUrl(
  key: string,
  version: number,
  width: number,
  theme: 'light' | 'dark'
): Promise<string> {
  const params = new URLSearchParams({ width: String(width), theme })
  const res = await fetch(`${BASE}${app(key)}/versions/${version}/shot?${params}`, { headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, t('global.request.imageFailed', { status: res.status }))
  return URL.createObjectURL(await res.blob())
}
