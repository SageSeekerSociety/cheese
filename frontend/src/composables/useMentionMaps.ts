// 正文渲染要用的三张查表：`<@handle>` 的名字、`<#id>` 的标题、`@` 前面那张脸的头像和
// 队友标记。渲染本身在 `../lib/renderMessage`，这里只把它们建出来交给调用方去填
// （填的人在 useChatPanel：名册来了写 names / identities，话题清单来了写 topicTitles）。
//
// 单独放在这里而不是就地建在 useChatPanel 里：每条消息正文都盯着这几张表，它们和
// useChatPanel 的其余部分没有别的来往 —— 挪出来，改一张表就不必动那个大文件。
import { reactive } from 'vue'

import { type MentionFaceMap } from '@/lib/memberActivity'

export function useMentionMaps() {
  const mentionNames = reactive<Record<string, string>>({})
  const mentionIdentities = reactive<MentionFaceMap>({})
  const topicTitles = reactive<Record<string, string>>({})
  const refMaps = { mentionNames, topicTitles, mentionIdentities }
  return { mentionNames, mentionIdentities, topicTitles, refMaps }
}
