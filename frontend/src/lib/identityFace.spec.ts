// 正文里那张小脸（lib/identityFace）：消息正文和文档的 chip 是 DOM 拼的，到不了 Vue
// 组件，所以这里单独测它把「这是谁」画成了什么。要守的几件事：人是圆的、队友是超椭圆；
// 首字母**永远先画好**（底色 + data-initial），头像只是压在上面的一层背景图 —— 这一层
// 走 background-image 而不是 <img>，坏图只是「什么都不画」，露出来的是首字母，而且这段
// markup 被序列化成 innerHTML 再插回去之后这条降级仍然成立；UserAvatar 那边记下的
// 「取不到」在这里也认，不再造一个必然破图的图。
import { AGENT_EYES, AGENT_TILE_PATH, avatarInitial } from '../utils/avatar'
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

  it('lays the picture over the initial, so the initial survives a failed image', () => {
    const face = identityFace({ handle: 'alice', name: 'Alice', avatar: 'https://x/avatars/7' })
    // 头像是一层背景图（不是 <img>）：坏图只是不画，底下那层首字母还在。
    expect(face.querySelector('img')).toBeNull()
    const picture = face.querySelector<HTMLElement>('.identity-face__img')
    expect(picture).not.toBeNull()
    expect(picture?.style.backgroundImage).toContain('https://x/avatars/7')
    // 首字母始终先画好：有头像地址时 data-initial 也在。
    expect(face.dataset.initial).toBe(avatarInitial('Alice'))
    expect(face.style.backgroundColor).not.toBe('')
  })

  it('keeps the initial fallback after the markup is serialized to HTML', () => {
    const face = identityFace({ handle: 'alice', name: 'Alice', avatar: 'https://x/avatars/7' })
    // renderPlain 会把这段 markup 序列化成 innerHTML 再插回去：error 监听跨不过那次
    // 序列化，只有「底下先画好了」在那条路上也成立。
    const box = document.createElement('div')
    box.innerHTML = face.outerHTML
    const rebuilt = box.firstElementChild as HTMLElement
    expect(rebuilt.outerHTML).toContain('data-initial=')
    expect(rebuilt.querySelector('.identity-face__img')).not.toBeNull()
    expect(rebuilt.querySelector('img')).toBeNull()
  })

  it('falls back to the initial for a URL already known to fail, without laying a picture', () => {
    rememberAvatarFailure('https://x/avatars/404')
    const face = identityFace({ handle: 'bob', name: 'Bob', avatar: 'https://x/avatars/404' })
    expect(face.querySelector('.identity-face__img')).toBeNull()
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

  it('draws a teammate with the same two capsule eyes as CheeseAvatar', () => {
    const face = identityFace({ handle: 'cheese-t1', name: '芝士', agent: true })
    const eyes = face.querySelectorAll('.identity-face__eye')
    expect(eyes.length).toBe(AGENT_EYES.length)
    AGENT_EYES.forEach((eye, i) => {
      expect(eyes[i].getAttribute('width')).toBe(String(eye.width))
      expect(eyes[i].getAttribute('height')).toBe(String(eye.height))
      expect(eyes[i].getAttribute('rx')).toBe(String(eye.rx))
    })
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
