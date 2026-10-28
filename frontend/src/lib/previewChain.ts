// 右栏小浏览器的连通链：一个跑着的应用要从读者这里走到页面，要过三段——平台（预览信息
// 读得到）、通道（队友那台电脑连着平台）、应用（端口有应答、页面没报错）。哪一段断了，
// 那一段说得出原因；后面几段没法判断，就是「等」，不是「断」。
//
// 这条链和原来那颗「出问题才亮」的小圆点是同一件事：链上任何一段不通就是那颗圆点，点开
// 看到的是每一段的原因。所以这里是唯一判「通不通」的地方。

import type { PreviewFrame } from '../composables/usePreviewFrames'

export type ChainLink = 'ok' | 'down' | 'wait'

//: 「通道在、应用不答」那一段的判据串。它在两处出现——造这一段的地方，和 `appAsleep`
//: 认它的地方——写成常量，改名时只动这一处，不会一边改了另一边还留着旧串。
const APP_SILENT = 'work.room.preview.chain.appSilent'

/** 页面自己报错（桥上不来、导航失败）那一段的判据串：地址栏那一行按它把这一段标红，
 *  所以导出去，省得两边各写一份字面量、改一处漏一处。 */
export const PAGE_ERROR = 'work.room.preview.chain.pageError'

/** 前一段还没通、这一段判不了。 */
const UNKNOWN = 'work.room.preview.chain.unknown'

export interface ChainSegment {
  key: 'platform' | 'tunnel' | 'app'
  state: ChainLink
  /** 这一段此刻的状况，一句话，i18n 键。 */
  reason: string
  /** 填进 reason 的参数。 */
  params?: Record<string, string>
}

export interface ChainInput {
  /** 读预览信息那一下失败了（平台连不上、接口报错）。 */
  platformError: string | null
  /** 这是一个跑着的应用（不是静态文件）。 */
  live: boolean
  tunnelUp: boolean
  /** 应用的端口有应答（后端给了地址）。 */
  appAnswering: boolean
  frame: PreviewFrame | null
  navigationError: string
  /** 这间房的队友叫什么：「通道」那一段说的是它的电脑。 */
  agentName: string
}

/** 三段（静态文件只有两段：平台、页面）各自怎样。
 *
 *  每一段各由一个函数造，只管好自己：平台不通时后面两段都是「等」，通道不通时应用那段也
 *  是「等」——只有前面都通了，后面那段的判据才有意义。 */
export function previewChain(input: ChainInput): ChainSegment[] {
  const platform = platformSegment(input)
  const platformOk = platform.state === 'ok'
  if (!input.live) return [platform, appSegment(input, platformOk)]
  const tunnel = tunnelSegment(input, platformOk)
  return [platform, tunnel, appSegment(input, platformOk && tunnel.state === 'ok')]
}

function platformSegment(input: ChainInput): ChainSegment {
  if (input.platformError) {
    return {
      key: 'platform',
      state: 'down',
      reason: 'work.room.preview.chain.platformDown',
      params: { error: input.platformError },
    }
  }
  return { key: 'platform', state: 'ok', reason: 'work.room.preview.chain.platformOk' }
}

function tunnelSegment(input: ChainInput, reachable: boolean): ChainSegment {
  if (!reachable) return { key: 'tunnel', state: 'wait', reason: UNKNOWN }
  if (!input.tunnelUp) {
    return {
      key: 'tunnel',
      state: 'down',
      reason: 'work.room.preview.chain.tunnelDown',
      params: { agent: input.agentName },
    }
  }
  return {
    key: 'tunnel',
    state: 'ok',
    reason: 'work.room.preview.chain.tunnelOk',
    params: { agent: input.agentName },
  }
}

/** 最后一段：跑着的应用是「端口应不应答」，静态文件是「页面自己有没有报错」。 */
function appSegment(input: ChainInput, reachable: boolean): ChainSegment {
  const { frame } = input
  if (!reachable) return { key: 'app', state: 'wait', reason: UNKNOWN }
  if (input.live && !input.appAnswering) {
    // 这一句里有 `{agent}`：通道那一段说的是谁的电脑，应用这一段也要说同一个人的电脑
    // （「电脑连着，但应用没有应答」里那个「电脑」是哪一台）。漏传就渲染成一个光秃秃的
    // 「的电脑」，所以两边一起给。
    return { key: 'app', state: 'down', reason: APP_SILENT, params: { agent: input.agentName } }
  }
  if (frame?.connection === 'gone') return { key: 'app', state: 'wait', reason: 'work.room.preview.chain.appRestarted' }
  if (frame?.runtime === 'failed' || frame?.runtimeError) {
    return { key: 'app', state: 'down', reason: PAGE_ERROR, params: { error: frame.runtimeError || '' } }
  }
  if (input.navigationError) {
    return { key: 'app', state: 'down', reason: PAGE_ERROR, params: { error: input.navigationError } }
  }
  if (frame?.connection === 'disconnected')
    return { key: 'app', state: 'wait', reason: 'work.room.preview.chain.appReconnecting' }
  return {
    key: 'app',
    state: 'ok',
    reason: frame?.runtime === 'ready' ? 'work.room.preview.chain.appReady' : 'work.room.preview.chain.appOk',
  }
}

/** 最后一段是「页面」还是「应用」：静态文件那条链只有两段（平台 › 页面），跑着的应用有
 *  三段。地址栏那一行要按它选名字，判据放在这里，跟造链的地方一起改。 */
export function isPageSegment(segments: ChainSegment[]): boolean {
  return segments.length === 2
}

/** 链上有没有一段**断**了：那颗圆点从「正常」切成「有问题」的判据。
 *
 *  「等」不算：重连中、跟着重启后的新实例走、前一段还没通所以后一段判不了，都是正常会
 *  经过的中间态，它们由圆点的琥珀档说，不换成那句「应用有问题」的提示——换的话每次重连
 *  都会亮一句有问题。 */
export function chainBroken(segments: ChainSegment[]): boolean {
  return segments.some((segment) => segment.state === 'down')
}

/** 「应用睡着了」这件事的判据，说到底就是这四个值：平台读得到、看的是个应用、通道在、
 *  应用有应答。少一个都算不出来。
 *
 *  两处都问它：画的那半（要不要出快照和唤醒卡）和取数的那半（要不要去平台上取快照）。
 *  各写一份的话，判据一改就会一边出了卡、另一边没去取，或者反过来白白多取一次。
 *  平台自己连不上不算——那时什么都判断不了。 */
export function appAsleepFrom(input: {
  platformOk: boolean
  live: boolean
  tunnelUp: boolean
  appAnswering: boolean
}): 'machine' | 'app' | null {
  if (!input.platformOk || !input.live) return null
  if (!input.tunnelUp) return 'machine'
  if (!input.appAnswering) return 'app'
  return null
}

/** 同上，从画好的那条链上读。静态文件那条链没有「通道」这一段，问不出睡着——答 null。 */
export function appAsleep(segments: ChainSegment[]): 'machine' | 'app' | null {
  const platform = segments.find((segment) => segment.key === 'platform')
  const tunnel = segments.find((segment) => segment.key === 'tunnel')
  const app = segments.find((segment) => segment.key === 'app')
  if (!platform || !tunnel) return null
  return appAsleepFrom({
    platformOk: platform.state === 'ok',
    live: true,
    tunnelUp: tunnel.state === 'ok',
    // 「应用不答」那一档：链上把它记成 down + `APP_SILENT`，页面自己报错是另一回事。
    appAnswering: !(app?.state === 'down' && app.reason === APP_SILENT),
  })
}
