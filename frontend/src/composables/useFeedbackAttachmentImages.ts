/**
 * 详情页上那几份附件的字节：取回来做成 `<img>` 能用的 object URL。
 *
 * **为什么这件事在 composable 里、不在画它的组件里**：附件端点按反馈的可见性判人，
 * 而 `<img src>` 发请求时带不上 Authorization（也读不到 localStorage），直接挂地址
 * 拿到的是 401 加一张裂图 —— 所以要先 fetch 再转 object URL。而那次 fetch 就是「够到
 * 取数层」这件事本身：画它的组件留在 A 档（只吃 props 出事件，能在组件预览站里单独
 * 渲染），取数归页面这一层。判据见 `docs/manual/dev/scenes.md`。
 *
 * object URL 不会自己消失：它把字节钉在内存里，直到被 revoke。一页可能有几张截图，
 * 所以换一条反馈时、以及卸载时都要把上一批还回去。
 */

import type { FeedbackAttachment } from '@/cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'

import { downloadFeedbackAttachment, feedbackAttachmentBlobUrl } from '@/api/feedback'

export function useFeedbackAttachmentImages(attachments: () => FeedbackAttachment[] | undefined) {
  /** 图片那一档的 object URL，按附件 id。**只有图片预取**：8MB 的日志没人想在看页面
   *  时先被下载一遍，而一张截图正是他点开这条反馈要看的东西。 */
  const urls = ref<Record<string, string>>({})
  /** 正在下载的那一份的 id（不是图片的那些走下载；图片本身用不上它）。 */
  const downloading = ref<string | null>(null)

  /** 第几轮在取。换了另一条反馈时，上一轮还在飞的请求回来不能把 `urls` 覆盖掉 ——
   *  覆盖了，刚取到的这一批对象地址就再也没人 revoke。 */
  let generation = 0

  function release(): void {
    for (const url of Object.values(urls.value)) URL.revokeObjectURL(url)
    urls.value = {}
  }

  async function load(): Promise<void> {
    const mine = ++generation
    release()
    const list = attachments() ?? []
    const images = list.filter((attachment) => attachment.content_type.startsWith('image/'))
    const loaded: Record<string, string> = {}
    await Promise.all(
      images.map(async (attachment) => {
        try {
          loaded[attachment.id] = await feedbackAttachmentBlobUrl(attachment.id)
        } catch {
          // 取不到就按「读不到」画（名字和大小还在，人知道少了什么）。**不弹全局
          // 错误**：一页里一张图 404 不该让整条反馈看起来坏了。
        }
      })
    )
    if (mine !== generation) {
      for (const url of Object.values(loaded)) URL.revokeObjectURL(url)
      return
    }
    urls.value = loaded
  }

  // 判的是那一**份**详情上的附件数组：它在这一条反馈读出来时就定了，所以同一条反馈
  // 里改别的东西（顶一下支持、发一条评论）不会重新拉一遍字节。
  watch(attachments, () => void load(), { immediate: true })
  onBeforeUnmount(release)

  async function download(attachment: FeedbackAttachment): Promise<void> {
    if (downloading.value) return
    downloading.value = attachment.id
    try {
      await downloadFeedbackAttachment(attachment)
    } finally {
      downloading.value = null
    }
  }

  return { urls, downloading, download }
}
