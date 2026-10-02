import type { Block } from '../cx_types'

import { request } from '../api'

// 选项问题 (cheese_ask): explicitly submitted answers.
//
// `expect_version` 是这次作答读到的那一版（初答 0，之后是 `answer_log` 末项的 `v`），
// `client_op_id` 是这次操作自己的 id —— 同一个 id 重试拿回的是同一版，不会记两次。
export type AnswerPayload = {
  kind: 'option' | 'note' | 'reject'
  option?: string
  note?: string
  expect_version: number
  client_op_id: string
}

export function answerOptions(blockId: string, payload: AnswerPayload, author: string): Promise<Block> {
  return request<Block>(`/topics/blocks/${encodeURIComponent(blockId)}/answers`, {
    method: 'POST',
    body: JSON.stringify({ author, ...payload }),
  })
}
