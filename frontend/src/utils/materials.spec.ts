// 头像/附件的地址拼装：一个漏配 API 前缀的构建不该串出相对地址。
//
// 为什么值得一条测试：`import.meta.env.VITE_API_BASE_URL` 漏配时是 JS 的 undefined，
// 老写法 `` `${undefined}/avatars/12` `` 会串出 `undefined/avatars/12` —— 一个**相对**
// 地址。浏览器拿它去拼当前页面地址，在设置页就成了
// `/users/settings/undefined/avatars/12`（dev 下 vite 的 /users 代理还会把它转给后端）
// → 404。本测试所跑的 vitest 环境也没有 `.env`，正好就是「漏配」这一格。
import { describe, expect, it } from 'vitest'

import { getAvatarUrl, getFullAttachmentUrl } from './materials'

describe('getAvatarUrl', () => {
  it('前缀缺失时兜底成网关挂载点，而不是把 undefined 串进地址', () => {
    expect(getAvatarUrl(12)).toBe('/api/avatars/12')
  })

  it('返回的永远是从根开始的绝对地址，不随当前路由漂移', () => {
    const url = getAvatarUrl(12)
    expect(url.startsWith('/')).toBe(true)
    expect(url).not.toContain('undefined')
  })

  it('没挑过头像时取默认图，同样带上前缀', () => {
    expect(getAvatarUrl(undefined)).toBe('/api/avatars/default')
  })
})

describe('getFullAttachmentUrl', () => {
  it('相对路径拼上前缀', () => {
    expect(getFullAttachmentUrl('/uploads/a.png')).toBe('/api/uploads/a.png')
  })

  it('已经是绝对地址的原样返回', () => {
    expect(getFullAttachmentUrl('https://cdn.example.com/a.png')).toBe('https://cdn.example.com/a.png')
  })
})
