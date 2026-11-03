// 「正在加载」那一行说到第几档了。
//
// 应用第一次连上（隧道还没起来、机器可能刚开机）和它已经跑着是两回事：热启动几秒就出页
// 面，冷启动实测要一两分钟。原来两处共用一个「正在加载新页面…」，读者等四十秒之后分不
// 出「快好了」和「卡住了」——两边看起来一模一样，而且看不到已经等了多久，只能一遍遍按
// 刷新。
//
// 这里把「等了多久」说成三档，档位线按实测取：几秒内是正常的（热启动），过了十来秒就要
// 说清慢在哪（多半是通道那头在冷启动），过了半分钟就说得出下一步能做什么。文案在 i18n。

/** 超过这么多秒就不再是「马上就好」：多半是应用那头的机器在起。 */
export const SLOW_AFTER_S = 12

/** 超过这么多秒就把话说到底：可以唤醒，也可以问问队友。 */
export const STUCK_AFTER_S = 45

export type LoadingTone = 'normal' | 'slow' | 'stuck'

/** 这一刻该用哪一档说话。 */
export function loadingTone(elapsedSeconds: number): LoadingTone {
  if (elapsedSeconds >= STUCK_AFTER_S) return 'stuck'
  if (elapsedSeconds >= SLOW_AFTER_S) return 'slow'
  return 'normal'
}

/** 等了多少秒，说成一句人话：一分多钟之后按分说，几十秒的时候按秒。 */
export function elapsedLabel(elapsedSeconds: number): { n: number; unit: 'second' | 'minute' } {
  const seconds = Math.max(0, Math.floor(elapsedSeconds))
  if (seconds < 60) return { n: seconds, unit: 'second' }
  return { n: Math.floor(seconds / 60), unit: 'minute' }
}
