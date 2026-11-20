// 「芝士会看到你在看什么」：发消息时右侧正开着哪一份文件。
//
// 读者说「这里不对」「这页太挤」，受话人得知道「这里」是哪一份。右栏开着一份文件时，
// 撰写框上写一行「芝士会看到：你在看 X」，发消息时这一份作为一条引用随消息走
// （`ViewingQuote`）。它是看得见、能关的：点那一行就不带，选择记在这台浏览器上。只在
// 发消息时带，不实时上报；这条消息已经指了一处（批注、引用）时不再叠一份。
//
// 谁提供：话题页（它同时拿着右栏和对话栏）。谁读：撰写框。没人提供时就是「什么也
// 没在看」，撰写框不显示那一行。
import type { ComputedRef, InjectionKey } from 'vue'
import type { ViewingQuote } from './quotedContext'

import { ref } from 'vue'

import { storageGet, storageSet } from './browserStorage'

export const VIEWING_CONTEXT: InjectionKey<ComputedRef<ViewingQuote | null>> = Symbol('viewing-context')

const KEY = 'cheese.composer.shareViewing'

/** 这台浏览器上读者选没选「带上我在看的」。默认带：那一行就写在撰写框上，看得见。 */
const share = ref(storageGet(KEY) !== 'off')

export function useShareViewing() {
  function toggle() {
    share.value = !share.value
    storageSet(KEY, share.value ? 'on' : 'off')
  }
  return { share, toggle }
}

export function fileName(path: string): string {
  return path.split('/').pop() || path
}

/** 面板页签对应的文件路径。资料库文档的页签是 `doc:<编号>`，那是服务端的名字，不是
 *  芝士读得到的文件，撰写框上也只会显示一串编号，所以不算「在看文件」。 */
export function tabFile(key: string, filePrefix: string, docPrefix: string): string | null {
  if (!key.startsWith(filePrefix)) return null
  const path = key.slice(filePrefix.length)
  return path && !path.startsWith(docPrefix) ? path : null
}
