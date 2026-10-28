// 一次下载的两头：把字节交给浏览器，和过后说一句它成了。
//
// 前端有五六个地方能下载（对话里的附件、资料库、预览、验收交付物、图片…），但它们
// 都收敛到 `api/download` 那一层少数几个取字节的函数上。曾经每一处各自点一下临时链接、
// 谁都不吭声：文件悄悄落进下载文件夹，界面一动不动，只有失败时才在某个角落里出现一行
// 红字。
//
// 现在这两件事都做在这一层，所以「哪一处下载」不再决定「下完说不说、失败怎么说」：
//
//   - 成不成只有点下那一下知道，所以成功那句话在这里说一次，上面十几个按钮一起有。
//   - 失败也在这里说，各调用点把那行红字撤掉，一次失败只说一遍。
//
// 提示用 sonner（和导出一份文档的提示同一套），因为下载完了人可能已经离开那个面板，
// 说一句话在哪儿都看得见，而且它自己会淡出，不用留一行红字占着位置。

import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'

/** 取一次下载的字节。
 *
 *  HTTP 拒绝了原样成形一句话抛出去（`下载失败（HTTP 403）`），调用方不用再管；
 *  请求根本没发出去时 fetch 抛的是浏览器自己的英文串（"Failed to fetch"），换成平台
 *  那句话——那种串端给人看等于什么都没说。 */
export async function downloadBytes(url: string, headers: Record<string, string>): Promise<Blob> {
  let res: Response
  try {
    res = await fetch(url, { headers })
  } catch {
    throw new Error(t('global.request.downloadUnreachable'))
  }
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  return res.blob()
}

/** 把一坨字节存成一份文件：临时链接点一下，剩下的交给浏览器。
 *
 *  存到哪个目录、要不要弹保存框都由浏览器（或桌面端的 webview）决定，页面管不着——
 *  所以成功那句话只说「下载目录」，不承诺具体路径。 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  saveUrl(url, filename)
  // 让这次点击先把字节拿走；提前 revoke 会让某些浏览器存下一份空文件。
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** 已经有一条临时链接时直接点它（图片另存那条路自己管着地址的生命周期）。 */
export function saveUrl(url: string, filename: string): void {
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  // 挂进文档再点：脱离文档的锚点在某些浏览器里点了不下载。
  document.body.appendChild(link)
  link.click()
  link.remove()
}

/** 一份文件已经交给浏览器了，说一句。
 *
 *  「交给浏览器」不等于「写进了磁盘」——将来某处改成弹保存框、人又点了取消，这句就会
 *  说错。现在没有那样一处，所以不为此加分支；真加了要一并改这里。 */
export function announceDownload(): void {
  toast.success(t('global.request.downloaded'))
}

/** 下载没成，说一句。调用点把各自那行红字撤掉，别让同一次失败说两遍。
 *
 *  给一句话就直接用那句（调用方换成了自己那句话），给一次抛出的错误就取它的
 *  `message`，都没有就用平台的兜底句。 */
export function announceDownloadFailure(cause: unknown): void {
  const said = typeof cause === 'string' ? cause : cause instanceof Error ? cause.message : ''
  toast.error(said || t('global.request.downloadUnreachable'))
}
