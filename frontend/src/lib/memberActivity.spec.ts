// 「一位成员的脸」怎么算（lib/memberActivity 的 `faceLookup`）：名册那张表认识他就用他
// 自己的那张，不认识就退回项目名册那一份 `avatarSrc`。项目名册那边也可能没有这一位 ——
// `avatarSrc` 回 null —— 那脸就得是 null，`IdentityView` 见到 null 才画那张按 handle
// 认人的彩色首字母。签名说 `avatarSrc` 只会回 string 时，这个 null 到不了这里。
import type { MentionFaceMap } from './memberActivity'

import { faceLookup } from './memberActivity'

describe('faceLookup', () => {
  it('uses the avatar and side the map knows, for someone it holds', () => {
    const map: MentionFaceMap = { alice: { avatar: '/avatars/7', agent: false } }
    expect(faceLookup(map, () => null)('alice')).toEqual({ avatar: '/avatars/7', agent: false })
  })

  it('leaves the avatar null when neither the map nor the roster knows the handle', () => {
    // 项目名册也不认识这一位：给 null，别给空串，也别拿一张不属于他的默认头像顶上。
    expect(faceLookup({}, () => null)('ghost')).toEqual({ avatar: null, agent: null })
  })
})
