/**
 * 浏览器推送的订阅（#1084 第 5 步）。
 *
 * 一轮活可以跑几十分钟到三小时，这期间人会关掉标签页、去开会、下班回家。推送是把
 * 他叫回来的那条路 —— 前提是他答应过。
 *
 * **权限只问一次，而且要问在对的时候。** 浏览器的推送权限被拒一次之后基本问不了
 * 第二次（`Notification.permission` 变成 `'denied'`，再调 `requestPermission` 直接
 * 返回 denied，不弹窗）。所以这里只提供动作，不决定时机；时机由房间里那条提示决定
 * （`PushPermissionPrompt.vue`）：等到这个人第一次真的遇到一轮跑过一分钟，那时他
 * 自己正等着结果，「完成后通知你」才是一句他听得懂的话。
 */
import { dropPushSubscription, pushPublicKey, savePushSubscription } from '@/api'

/** 这个浏览器有没有这套能力。装在 iOS 主屏之外的 Safari 就没有。 */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  )
}

/** 已经问过了吗 —— 拒绝和同意都算问过。 */
export function permissionSettled(): boolean {
  return pushSupported() && Notification.permission !== 'default'
}

/** VAPID 公钥是 base64url 的，而 `applicationServerKey` 要一段字节。 */
function decodeKey(base64url: string): ArrayBuffer {
  const padded = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4))
  // 直接给 ArrayBuffer：`Uint8Array.from` 回来的是 `Uint8Array<ArrayBufferLike>`，
  // 而 `BufferSource` 不收那个（它要一个确定由 ArrayBuffer 支撑的视图）。
  const buffer = new ArrayBuffer(raw.length)
  const view = new Uint8Array(buffer)
  for (let i = 0; i < raw.length; i += 1) view[i] = raw.charCodeAt(i)
  return buffer
}

/** 订阅对象里那两把加密材料，转成后端要的 base64url 字符串。 */
function encodeKey(subscription: PushSubscription, name: 'p256dh' | 'auth'): string {
  const raw = subscription.getKey(name)
  if (!raw) return ''
  let binary = ''
  for (const byte of new Uint8Array(raw)) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** 这个部署开了推送吗（后端配了 VAPID 密钥）。 */
export async function pushAvailable(): Promise<boolean> {
  if (!pushSupported()) return false
  try {
    return (await pushPublicKey()).available
  } catch {
    return false
  }
}

/**
 * 问权限、订阅、把订阅交给后端。返回有没有成到。
 *
 * 必须由一次用户手势触发（点了「开启通知」那个按钮）—— 浏览器只在那种时候才肯弹
 * 权限框。
 */
export async function enablePush(): Promise<boolean> {
  if (!pushSupported()) return false
  try {
    const { key, available } = await pushPublicKey()
    if (!available || !key) return false

    const permission = await Notification.requestPermission()
    if (permission !== 'granted') return false

    const registration = await navigator.serviceWorker.ready
    // 已经订阅过就沿用那一个：重复 subscribe 会因为 applicationServerKey 不同而
    // 直接抛错，而它多半本来就是同一个 —— 只是后端那一行可能丢了（换了部署、清过
    // 库），所以照样交一次。
    const existing = await registration.pushManager.getSubscription()
    const subscription =
      existing ??
      (await registration.pushManager.subscribe({
        // 浏览器要求我们承诺「收到推送必定显示一条通知」。它会核对：反复静默接收
        // 会让它先替我们显示一条「此站点在后台更新」，再往后直接撤权限。
        userVisibleOnly: true,
        applicationServerKey: decodeKey(key),
      }))

    await savePushSubscription({
      endpoint: subscription.endpoint,
      p256dh: encodeKey(subscription, 'p256dh'),
      auth: encodeKey(subscription, 'auth'),
      user_agent: navigator.userAgent.slice(0, 255),
    })
    return true
  } catch {
    // 推送是个附加渠道：开不起来不该让调用它的那个界面出错。站内通知和邮件照旧。
    return false
  }
}

/** 退订这个浏览器：本地取消，并告诉后端别再往这个地址发。 */
export async function disablePush(): Promise<void> {
  if (!pushSupported()) return
  try {
    const registration = await navigator.serviceWorker.ready
    const subscription = await registration.pushManager.getSubscription()
    if (!subscription) return
    await dropPushSubscription(subscription.endpoint)
    await subscription.unsubscribe()
  } catch {
    // 同上：退订失败最坏的后果是后端往一个死地址发几次，投递侧会按 404/410 自己
    // 把它删掉。
  }
}
