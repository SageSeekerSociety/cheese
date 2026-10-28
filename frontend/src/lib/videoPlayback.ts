// 视频播放器要算的那几样：时间怎么念、倍速往哪转、这台设备该不该省着点。
//
// 放在 lib 里，是因为这些判断要在组件外面也说得清、也测得着：`AttachmentVideo` 的画法
// 和它的测试读的是同一个答案，不各写一份。

/** 播放器给的几档倍速。原型上是 1×/1.25×/1.5×/2×，这里补了 0.5×：录屏里讲得快的
 *  片段，慢放比反复拖进度条更有用。 */
export const VIDEO_RATES = [0.5, 1, 1.25, 1.5, 2] as const

/**
 * 秒数念成播放器上的那种时长：`0:07`、`1:02`、`1:02:03`。
 *
 * 读不到（`NaN`、负数、`null`）一律念 `0:00`：元数据还没回来时控件上不该空着或出现
 * `NaN:NaN`，那是「坏了」的样子，不是「还没好」。
 */
export function formatVideoTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '0:00'
  }
  const whole = Math.floor(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const rest = whole % 60
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes)
  const ss = String(rest).padStart(2, '0')
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

/** 点一下倍速按钮之后的那一档：在 `VIDEO_RATES` 里转一圈。 */
export function nextVideoRate(rate: number): number {
  const index = VIDEO_RATES.indexOf(rate as (typeof VIDEO_RATES)[number])
  return VIDEO_RATES[(index + 1) % VIDEO_RATES.length]
}

/**
 * 这台设备、这条网络该不该省着点：读者开了省流，或者网差到 2G 那一档。
 *
 * `navigator.connection` 不是标准 API（Chrome/Edge 有，Firefox/Safari 没有），所以
 * 参数是「读得到就传，读不到就传 null」，读不到按不省流处理——不能因为读不到就把
 * 所有人的视频都扣着不加载。
 */
export function prefersLightVideo(
  connection: { saveData?: boolean; effectiveType?: string } | null | undefined
): boolean {
  if (!connection) return false
  if (connection.saveData) return true
  return connection.effectiveType === 'slow-2g' || connection.effectiveType === '2g'
}

/** `navigator.connection` 的最小形状：免得在组件里到处断言这个非标准字段。 */
export function connectionInfo(): { saveData?: boolean; effectiveType?: string } | null {
  if (typeof navigator === 'undefined') return null
  const connection = (navigator as Navigator & {
    connection?: { saveData?: boolean; effectiveType?: string }
  }).connection
  return connection ?? null
}
