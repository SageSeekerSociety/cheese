// 右栏小浏览器的连通链：一个跑着的应用要从读者这里走到页面，要过三段——平台（预览信息
// 读得到）、通道（队友那台电脑连着平台）、应用（端口有应答、页面没报错）。哪一段断了，
// 那一段说得出原因；后面几段没法判断，就是「等」，不是「断」。
//
// 这条链和原来那颗「出问题才亮」的小圆点是同一件事：链上任何一段不通就是那颗圆点，点开
// 看到的是每一段的原因。所以这里是唯一判「通不通」的地方。

import type { PreviewFrame } from '../composables/usePreviewFrames'

export type ChainLink = 'ok' | 'down' | 'wait'

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

/** 三段（静态文件只有两段：平台、页面）各自怎样。 */
export function previewChain(input: ChainInput): ChainSegment[] {
  const { frame } = input
  const platform: ChainSegment = input.platformError
    ? {
        key: 'platform',
        state: 'down',
        reason: 'work.room.preview.chain.platformDown',
        params: { error: input.platformError },
      }
    : { key: 'platform', state: 'ok', reason: 'work.room.preview.chain.platformOk' }
  const app = appSegment(input, platform.state === 'ok')
  if (!input.live) return [platform, app]
  const tunnel: ChainSegment =
    platform.state !== 'ok'
      ? { key: 'tunnel', state: 'wait', reason: 'work.room.preview.chain.unknown' }
      : input.tunnelUp
        ? { key: 'tunnel', state: 'ok', reason: 'work.room.preview.chain.tunnelOk', params: { agent: input.agentName } }
        : {
            key: 'tunnel',
            state: 'down',
            reason: 'work.room.preview.chain.tunnelDown',
            params: { agent: input.agentName },
          }
  if (tunnel.state !== 'ok')
    return [platform, tunnel, { key: 'app', state: 'wait', reason: 'work.room.preview.chain.unknown' }]
  if (!input.appAnswering)
    return [platform, tunnel, { key: 'app', state: 'down', reason: 'work.room.preview.chain.appSilent' }]
  if (frame?.connection === 'gone')
    return [platform, tunnel, { key: 'app', state: 'wait', reason: 'work.room.preview.chain.appRestarted' }]
  return [platform, tunnel, app]
}

function appSegment(input: ChainInput, reachable: boolean): ChainSegment {
  const { frame } = input
  if (!reachable) return { key: 'app', state: 'wait', reason: 'work.room.preview.chain.unknown' }
  if (frame?.runtime === 'failed' || frame?.runtimeError) {
    return {
      key: 'app',
      state: 'down',
      reason: 'work.room.preview.chain.pageError',
      params: { error: frame.runtimeError || '' },
    }
  }
  if (input.navigationError) {
    return {
      key: 'app',
      state: 'down',
      reason: 'work.room.preview.chain.pageError',
      params: { error: input.navigationError },
    }
  }
  if (frame?.connection === 'disconnected')
    return { key: 'app', state: 'wait', reason: 'work.room.preview.chain.appReconnecting' }
  return {
    key: 'app',
    state: 'ok',
    reason: frame?.runtime === 'ready' ? 'work.room.preview.chain.appReady' : 'work.room.preview.chain.appOk',
  }
}

/** 链上有没有一段不通：那颗圆点亮不亮。 */
export function chainBroken(segments: ChainSegment[]): boolean {
  return segments.some((segment) => segment.state !== 'ok')
}

/** 应用睡着了：通道那一段断了（队友的电脑休眠或还没起），或者通道在但应用不答。
 *  这时页面上显示静态快照和「唤醒」那张卡；平台自己连不上不算——那时什么都判断不了。 */
export function appAsleep(segments: ChainSegment[]): 'machine' | 'app' | null {
  const tunnel = segments.find((segment) => segment.key === 'tunnel')
  const app = segments.find((segment) => segment.key === 'app')
  if (!tunnel) return null
  if (tunnel.state === 'down') return 'machine'
  if (tunnel.state === 'ok' && app?.state === 'down' && app.reason === 'work.room.preview.chain.appSilent') return 'app'
  return null
}
