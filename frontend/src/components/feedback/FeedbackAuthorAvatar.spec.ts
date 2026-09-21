/**
 * 「谁写的」那一格画的是谁的脸。
 *
 * 两个断言各钉住一半契约，缺任何一半都会静默变坏：
 *
 * 1. `author_avatar_id` 有值时必须拼出 `/avatars/{id}`。接口回的是素材 id 而不是
 *    现成的 URL（平台惯例，见 `utils/materials.getAvatarUrl`），拼错了不会报错 ——
 *    那一屏看起来只是「大家都用首字母」。
 * 2. `author_avatar_id` 为 null 时必须画彩色首字母，**一张图都不能取**。后端已经把
 *    「注册时人人被写上的那张全局默认头像」判掉了（`chosen_avatar_ids`），所以这个
 *    字段为空就是「这个人没挑过」。退回 `/avatars/default` 会让所有没挑过的人长成
 *    同一张脸；按 handle 派生的首字母至少彼此不同 —— 区分人正是头像唯一的活。
 *    `getAvatarUrl` 对空值返回的正是那张默认图，所以这一条测的是「有没有人绕过判空」。
 *
 * agent 那一支不看 id：agent 没有自己挑的图，它的标记是 `CheeseAvatar`（深色方块 +
 * 名字首字），上面传什么 id 都不该变成一张图片。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import FeedbackAuthorAvatar from './FeedbackAuthorAvatar.vue'

const vuetify = createVuetify({ components, directives })

function mountAvatar(props: Record<string, unknown>) {
  return render(FeedbackAuthorAvatar, {
    props: { handle: 'andy', ...props },
    global: { plugins: [vuetify] },
  })
}

describe('FeedbackAuthorAvatar', () => {
  it('挑了头像就取那个人自己那张图', () => {
    const { container } = mountAvatar({ avatarId: 7 })
    const img = container.querySelector('img')
    expect(img, '挑了头像却没画图片').not.toBeNull()
    expect(img?.getAttribute('src')).toContain('/avatars/7')
  })

  it('没挑过头像就画彩色首字母，一张图都不取', () => {
    const { container, getByText } = mountAvatar({ avatarId: null })
    expect(
      container.querySelector('img'),
      '不该去取 /avatars/default —— 那会让所有没挑过头像的人共用同一张脸'
    ).toBeNull()
    expect(getByText('A')).toBeTruthy()
  })

  it('agent 画的是那个深色方块，不是一张图片', () => {
    const { container } = mountAvatar({ isAgent: true, avatarId: 7 })
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('.cheese-avatar')).not.toBeNull()
  })
})
