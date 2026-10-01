/**
 * 带选项的问题：在房间里问一道，点一个选项答一道。
 *
 * 问：发成了请求一回来就上屏，不等那一帧（帧到了按 id 认出是同一条）；没发成就把
 * 错误抛回提问框，框不关、字都在。答：一次只答一道，答案回到问的那一方手上；答过
 * 的样子所有人都看得见。
 *
 * 往时间线上放哪一条、错误怎么说，是房间壳的事，从外面交进来。
 */

import type { Block } from '../cx_types'

import { ref } from 'vue'

import { answerOptions, askRoom } from '../api/optionQuestions'

import { t } from '@/i18n'

export function useOptionQuestions(opts: {
  topicId: () => string | undefined
  /** 回答时署的名字（后端认凭据，没有凭据时才读它）。 */
  author: string
  /** 刚问出的这一条放上时间线。 */
  push: (block: Block) => void
  /** 把答过的这一条换进时间线（在的话）。 */
  show: (block: Block) => void
  /** 没答上：告诉看的人。 */
  fail: (error: unknown) => void
}) {
  const askBusy = ref<string | null>(null)

  async function pickOption(m: Block, option: string) {
    if (askBusy.value) return
    askBusy.value = m.id
    try {
      opts.show(await answerOptions(m.id, option, opts.author))
    } catch (e) {
      opts.fail(e)
    } finally {
      askBusy.value = null
    }
  }

  async function askQuestion(question: string, options: string[]): Promise<void> {
    const id = opts.topicId()
    if (!id) throw new Error(t('work.room.ask.failed'))
    opts.push(await askRoom(id, question, options))
  }

  return { askBusy, pickOption, askQuestion }
}
