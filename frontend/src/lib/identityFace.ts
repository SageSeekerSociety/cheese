// 「这是谁」在 DOM 里的那一份脸：Vue 那边是 components/common/IdentityView.vue，这里给
// 走不了 Vue 的地方用 —— 一条消息的正文、一段文档，chip 是 lib/refChip 直接拼出来的
// DOM，组件到不了那里（样式所以在全局 style.css 的 `.identity-face`，scoped 到不了）。
//
// 同一套规矩：给 handle 就认得出人；有头像地址用图，没有画按 handle 派生的底色首字母
// （utils/avatar 的 avatarColor / avatarInitial）；队友画超椭圆（同一条 AGENT_TILE_PATH，
// 底色按 agentTone 取一档），人和队友靠形状分开。名字读屏那边读的是 chip 上那个 @名字，
// 所以这张脸是装饰，aria-hidden。
import { AGENT_TILE_PATH, agentTone, avatarColor, avatarInitial } from '../utils/avatar'
import { isAvatarKnownFailed } from '../utils/avatarFailures'

import { isAgentHandle } from './authorship'

const SVG_NS = 'http://www.w3.org/2000/svg'

export interface FaceIdentity {
  /** 认人的唯一依据：底色的种子，也是判「是不是队友」的兜底。 */
  handle: string
  /** 显示名；不给就退回 handle。 */
  name?: string | null
  /** 自己挑过的头像地址（名册上那一位的）；没挑过、或名册上没他时 null。 */
  avatar?: string | null
  /** 是不是 AI 队友。名册在手就照名册填；不给则按 handle 命名规则兜底。 */
  agent?: boolean | null
  /** 边长（px）。 */
  size?: number
}

/**
 * 一枚内联的小头像，给 chip / 正文用。人是圆的、队友是超椭圆；都没挑过头像时人是
 * 彩色首字母、队友是纯底色。纯 DOM，不 fetch：头像地址由调用方从名册上取好传进来。
 */
export function identityFace(identity: FaceIdentity): HTMLElement | SVGElement {
  const size = identity.size ?? 16
  const label = identity.name || identity.handle
  const isAgent = identity.agent ?? isAgentHandle(identity.handle)

  if (isAgent) {
    const svg = document.createElementNS(SVG_NS, 'svg')
    svg.setAttribute('class', 'identity-face identity-face--agent')
    svg.setAttribute('width', String(size))
    svg.setAttribute('height', String(size))
    svg.setAttribute('viewBox', '0 0 100 100')
    svg.setAttribute('aria-hidden', 'true')
    svg.dataset.tone = String(agentTone(identity.handle || label))
    const tile = document.createElementNS(SVG_NS, 'path')
    tile.setAttribute('d', AGENT_TILE_PATH)
    svg.append(tile)
    return svg
  }

  const face = document.createElement('span')
  face.className = 'identity-face identity-face--person'
  face.setAttribute('aria-hidden', 'true')
  face.style.width = `${size}px`
  face.style.height = `${size}px`
  face.style.fontSize = `${Math.round(size * 0.56)}px`
  // 取不到的地址（UserAvatar 那边已经记下了）不再造一个必然破图的 img，直接走首字母——
  // 和 UserAvatar 读同一份记忆（utils/avatarFailures），同一张脸到哪儿都是同一张。
  if (identity.avatar && !isAvatarKnownFailed(identity.avatar)) {
    const img = document.createElement('img')
    img.className = 'identity-face__img'
    img.setAttribute('src', identity.avatar)
    img.setAttribute('alt', '')
    face.append(img)
  } else {
    // 首字母走 ::before 的 content，不进 textContent：chip 的文字仍然是干净的「@名字」，
    // 老测试和复制出来的正文都不多一个字母。
    face.style.backgroundColor = avatarColor(identity.handle || label)
    face.dataset.initial = avatarInitial(label)
  }
  return face
}
