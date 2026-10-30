// 一次下载的后半截：字节拿到手之后，交给浏览器，和过后说一句它成了。
//
// 前端的下载入口有十几个（对话里的附件、资料库、预览、验收交付物、图片…），它们各自
// 取字节（`api/roomFiles.ts` 那些），但**存盘和说话是同一件事**，所以放在这里：
//
//   - 成不成只有点下那一下知道，成功那句话在这里说一次，上面十几个按钮一起有。
//   - 失败也在这里说，各调用点把那行红字撤掉，一次失败只说一遍。
//
// 这一层**不取字节**（不 `fetch`）：组件要 import 它，而 `frontend_grade.py` 把带
// `fetch(` 的模块算作 API 层，组件引了就等于自己连了后端，会把这个组件的「独立可挂载」
// 等级打下去（`pnpm run lint:scenes` 会红）。所以取字节留在 `api/**` 那一侧，这里只
// 管拿到之后的事。
//
// 提示用 sonner（和导出一份文档的提示同一套），因为下载完了人可能已经离开那个面板，
// 说一句话在哪儿都看得见，而且它自己会淡出，不用留一行红字占着位置。

import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'

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
 *  给一句话就直接用那句，给一次抛出的错误就取它的 `message`（取字节那一层已经把它们
 *  说成了人话），都没有就用平台的兜底句。 */
export function announceDownloadFailure(cause: unknown): void {
  const said = typeof cause === 'string' ? cause : cause instanceof Error ? cause.message : ''
  toast.error(said || t('global.request.downloadUnreachable'))
}
