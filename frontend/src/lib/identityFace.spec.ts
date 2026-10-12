// 正文里那张小脸（lib/identityFace）：消息正文和文档的 chip 是 DOM 拼的，到不了 Vue
// 组件，所以这里单独测它把「这是谁」画成了什么。要守的三件事：人是圆的、队友是超椭圆；
// 没头像地址就画首字母，但首字母**不进 textContent**（复制正文、老断言都不该多一个字）；
// UserAvatar 那边记下的「取不到」在这里也认，不再造一个必然破图的 img。
import { AGENT_TILE_PATH, avatarInitial } from '../utils/avatar'
import { rememberAvatarFailure } from '../utils/avatarFailures'

import { identityFace } from './identityFace'

describe('identityFace', () => {
  it('draws a person with no picture as a round initial that stays out of the text', () => {
    const face = identityFace({ handle: 'alice', name: 'Alice', avatar: null })
    expect(face.tagName).toBe('SPAN')
    expect(face.getAttribute('class')).toContain('identity-face--person')
    expect(face.dataset.initial).toBe(avatarInitial('Alice'))
    expect(face.getAttribute('aria-hidden')).toBe('true')
    // 首字母走 ::before 的 content，不进 textContent。
    expect(face.textContent).toBe('')
    // 底色按 handle 取，不按昵称 —— 改个昵称不该换颜色。
    expect(face.style.backgroundColor).not.toBe('')
  })

  it('uses the picture when there is one', () => {
    const face = identityFace({ handle: 'alice', name: 'Alice', avatar: 'https://x/avatars/7' })
    const img = face.querySelector('img')
    expect(img?.getAttribute('src')).toBe('https://x/avatars/7')
    expect(img?.getAttribute('alt')).toBe('')
    expect(face.dataset.initial).toBeUndefined()
  })

  it('falls back to the initial for a URL already known to fail, without asking again', () => {
    rememberAvatarFailure('https://x/avatars/404')
    const face = identityFace({ handle: 'bob', name: 'Bob', avatar: 'https://x/avatars/404' })
    expect(face.querySelector('img')).toBeNull()
    expect(face.dataset.initial).toBe(avatarInitial('Bob'))
  })

  it('draws a teammate as the superellipse, never a round initial', () => {
    const face = identityFace({ handle: 'cheese-t1', name: '芝士', agent: true })
    expect(face.tagName.toLowerCase()).toBe('svg')
    expect(face.getAttribute('class')).toContain('identity-face--agent')
    expect(face.getAttribute('aria-hidden')).toBe('true')
    expect(face.dataset.tone).toBeDefined()
    expect(face.querySelector('path')?.getAttribute('d')).toBe(AGENT_TILE_PATH)
    expect(face.textContent).toBe('')
  })

  it('falls back to the handle naming rule when the caller did not say who is a teammate', () => {
    expect(identityFace({ handle: 'cheese-t2' }).tagName.toLowerCase()).toBe('svg')
    expect(identityFace({ handle: 'alice' }).tagName).toBe('SPAN')
  })

  it('takes the size it was given', () => {
    const face = identityFace({ handle: 'alice', name: 'Alice', size: 20 })
    expect(face.style.width).toBe('20px')
    expect(face.style.height).toBe('20px')
  })
})
