// 「这是谁」在 DOM 里的那一份脸：Vue 那边是 components/common/IdentityView.vue，这里给
// 走不了 Vue 的地方用 —— 一条消息的正文、一段文档，chip 是 lib/refChip 直接拼出来的
// DOM，组件到不了那里（样式所以在全局 style.css 的 `.identity-face`，scoped 到不了）。
//
// 同一套规矩：给 handle 就认得出人；队友画超椭圆（同一条 AGENT_TILE_PATH，底色按
// agentTone 取一档），人和队友靠形状分开。**首字母永远先画好、头像压在上面**：这是为了
// 让「图取不到」在序列化之后仍是首字母（详见下面那颗 picture 的注释）。名字读屏那边读的
// 是 chip 上那个 @名字，所以这张脸是装饰，aria-hidden。
import { AGENT_EYES, AGENT_TILE_PATH, agentTone, avatarColor, avatarInitial } from '../utils/avatar'
import { isAvatarKnownFailed, probeAvatarFailure } from '../utils/avatarFailures'

import { isAgentHandle } from './authorship'

const SVG_NS = 'http://www.w3.org/2000/svg'

/** 头像地址包成一条安全的 CSS url()：只防引号和反斜杠，别让地址咬断这条声明。 */
function cssUrl(url: string): string {
  // 转义单独一行：正则里的 `"` 若写在模板的 `${}` 里，会教 i18n 扫描器把它当成
  // 字符串开头（scanInterpolation 不认正则字面量），后面整份文件跟着错位。
  const escaped = url.replace(/["\\]/g, '\\$&')
  return `url("${escaped}")`
}

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
    // 一双胶囊眼（一大一小、偏右）—— 几何取自 utils/avatar 的 AGENT_EYES，和 CheeseAvatar
    // 共用同一份，全平台一个队友到哪儿都是同一种脸（brand.md §10）。
    const eyes = document.createElementNS(SVG_NS, 'g')
    eyes.setAttribute('class', 'identity-face__eyes')
    for (const eye of AGENT_EYES) {
      const rect = document.createElementNS(SVG_NS, 'rect')
      rect.setAttribute('class', 'identity-face__eye')
      rect.setAttribute('x', String(eye.x))
      rect.setAttribute('y', String(eye.y))
      rect.setAttribute('width', String(eye.width))
      rect.setAttribute('height', String(eye.height))
      rect.setAttribute('rx', String(eye.rx))
      eyes.append(rect)
    }
    svg.append(eyes)
    return svg
  }

  const face = document.createElement('span')
  face.className = 'identity-face identity-face--person'
  face.setAttribute('aria-hidden', 'true')
  face.style.width = `${size}px`
  face.style.height = `${size}px`
  face.style.fontSize = `${Math.round(size * 0.56)}px`
  // 首字母**永远先画好**（底色 + ::before 的 content），头像只是压在上面的一层背景图。
  // 这样降级不依赖任何 JS：这段 markup 会被 renderPlain 序列化成 innerHTML 再插回去，
  // 挂在元素上的 error 监听跨不过那次序列化，只有「底下先画好了」在那条路上也成立。
  // 坏图对 background-image 只是「什么都不画」，不会像 <img> 那样露出破图图标 —— 露出
  // 来的正是下面那层首字母。首字母走 ::before、不进 textContent，chip 的文字仍是干净的
  // 「@名字」，老测试和复制出来的正文都不多一个字母。
  face.style.backgroundColor = avatarColor(identity.handle || label)
  face.dataset.initial = avatarInitial(label)
  if (identity.avatar && !isAvatarKnownFailed(identity.avatar)) {
    const picture = document.createElement('span')
    picture.className = 'identity-face__img'
    picture.setAttribute('aria-hidden', 'true')
    picture.style.backgroundImage = cssUrl(identity.avatar)
    face.append(picture)
    // 背景图坏了不触发任何事件，派一次探针把「取不到」补记回和 UserAvatar 同一份记忆
    // （utils/avatarFailures）：下次渲染直接不画这一层，也不再要一次必然失败的图。
    probeAvatarFailure(identity.avatar)
  }
  return face
}
