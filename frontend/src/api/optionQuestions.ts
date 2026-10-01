// 带选项的问题：房间里任何一位成员都能问（AI 队友用 `cheese_ask`，人在输入框旁边），
// 别人点一个选项就是回答。问的人不答自己的题。
import type { Block } from '../cx_types'

import { request } from '../api'

export function askRoom(topicId: string, question: string, options: string[]): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/ask`, {
    method: 'POST',
    body: JSON.stringify({ question, options }),
  })
}

export function answerOptions(blockId: string, option: string, author: string): Promise<Block> {
  return request<Block>(`/topics/blocks/${encodeURIComponent(blockId)}/answer`, {
    method: 'POST',
    body: JSON.stringify({ option, author }),
  })
}
