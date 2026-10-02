import type { ChatAttachment, FileContent } from '@/cx_types'
import type { DocumentIdentity, DocumentSnapshot } from '@/lib/documentBytes'
import type { SubmitPreviewQuestion } from '@/lib/previewQuestion'
import type { PagePin, SlideSource } from './slidesContext'

import { ref } from 'vue'

import { t } from '@/i18n'
import { sameDocumentIdentity } from '@/lib/documentBytes'
import { isQuotedContext } from '@/lib/quotedContext'

interface PagePinProps {
  topicId: string | null
  submitQuestion?: SubmitPreviewQuestion
  uploadAnnotation?: (topicId: string, image: { blob: Blob; filename: string }) => Promise<ChatAttachment>
  previewFile: FileContent | null
  docIdentity?: DocumentIdentity | null
  docSnapshot?: DocumentSnapshot | null
  docBytes: ArrayBuffer | null
  slideContext?: SlideSource
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
}

interface PagePinDeps {
  /** 这一页现在长什么样；给不出图就当没有。 */
  snapshot: (page: number) => Promise<{ blob: Blob } | null>
  /** 指了一处：把「文件 + 位置 + 原文」那三样摆进输入框。 */
  open: (label: string, quote: string, address: string) => void
  clear: () => void
}

/** 指着页面上的一点说一句话。
 *
 *  页面上下文什么时候还能信，和图片那一格是同一套判据：比的不只是拿到的元数据，
 *  还有带着那份上下文的字节此刻真的显示着——元数据自己授权不了任何东西。
 *  发出去的那条消息随行带上这一页当时的图，理由见 `send`。 */
export function usePreviewPagePin(props: PagePinProps, deps: PagePinDeps) {
  const pin = ref<PagePin | null>(null)

  /** 这份页面上下文此刻还能不能用：身份、字节、渲染三样都对得上才算。 */
  function canUse(context: SlideSource): boolean {
    const expected = props.slideContext
    const current = props.docIdentity
    const displayed = props.docSnapshot
    if (
      !expected ||
      !current ||
      !displayed ||
      props.docBytes !== displayed.bytes ||
      props.docLoading ||
      props.docError ||
      props.docRendererMissing ||
      props.topicId !== current.topicId ||
      props.previewFile?.path !== current.path ||
      props.previewFile?.version !== current.version ||
      (props.previewFile?.source ?? 'live') !== current.source
    )
      return false
    const identity = { ...context, taskId: context.taskId ?? null }
    return (
      sameDocumentIdentity(identity, current) &&
      sameDocumentIdentity(current, displayed.identity) &&
      sameDocumentIdentity({ ...expected, taskId: expected.taskId ?? null }, current) &&
      displayed.sourceVersion === current.version
    )
  }

  /** 页面上的一点：位置说的是这一页的哪个比例，交出去的是带版本的那一点。
   *
   *  比例换算成百分数是给人看的，那个数字和输入框里那句话是同一份东西，不重新算。 */
  function onPin(payload: PagePin) {
    if (!canUse(payload.context)) return
    const where = t('work.room.preview.pinWhere', {
      left: Math.round(payload.x * 100),
      top: Math.round(payload.y * 100),
    })
    deps.open(t('work.room.preview.page', { page: payload.page }), where, '')
    pin.value = { ...payload, context: { ...payload.context } }
  }

  /** 指出的一点发出去：那条消息带着它依据的那一版文件身份，随行带上这一页当时的图。
   *
   *  配图不是装饰：位置本身是「第 3 页 42% 处」，受话人拿这句话去原始文件里找，找到
   *  的是同一页没错，但上一版和这一版之间那一处可能整个挪过位。图是发出去的那一刻
   *  屏幕上那一页的样子，看出来的是同一件事。
   *
   *  上传要等一会儿，等回来再核一次房间和版本：等的时候人可能换了房间、文件可能被
   *  芝士改了，那时宁可不发，也不能配着一张说的不是它的图发出去。 */
  async function send(note: string) {
    const target = pin.value
    const topicId = props.topicId
    const submit = props.submitQuestion
    if (!target || !topicId || !submit || !canUse(target.context)) return
    const quotedContext = {
      kind: 'page-pin' as const,
      path: target.context.path,
      source: target.context.source,
      version: target.context.version,
      task_id: target.context.taskId ?? null,
      page: target.page,
      x: target.x,
      y: target.y,
    }
    // 发之前照同一把尺子量一遍自己：不合规的消息后端会 422 丢掉，而那一下图已经
    // 传上去了，白传。量在传图之前。
    if (!isQuotedContext(quotedContext)) return
    let attachments: ChatAttachment[] | undefined
    const upload = props.uploadAnnotation
    const shot = await deps.snapshot(target.page)
    if (upload && shot) {
      try {
        attachments = [await upload(topicId, { blob: shot.blob, filename: `page-${target.page}.png` })]
      } catch {
        // 图没传上去就不带图：位置那句话自己站得住。
        attachments = undefined
      }
    }
    if (props.topicId !== topicId || !canUse(target.context)) return
    const accepted = submit({
      intent: 'ask-agent',
      topicId,
      content: note,
      attachments,
      quotedContext,
    })
    if (accepted) deps.clear()
  }

  return { pin, canUse, onPin, send }
}
