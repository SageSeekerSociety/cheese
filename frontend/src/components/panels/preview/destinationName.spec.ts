/**
 * 同一个去处，两处不能叫两个名字。
 *
 * 指一处之后交给任务芝士的那件事，读者在两个地方读到它的名字：卡上那颗去处下拉，和待发
 * 篮每一行的去处标签。两处一度一个叫「发给芝士」、一个叫「待发给任务芝士」——同一个去处
 * 两个名字。原型里它叫「发给任务芝士」。
 *
 * 两边都真渲染出来比：比的是读者看到的那串字（行为），不是读源码。只钉两边一致，不钉具体
 * 用哪个词——用词是文案的事，一致才是这段代码的事。
 */
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import PreviewLocator from './PreviewLocator.vue'

import PendingNotesTray from '@/components/room/PendingNotesTray.vue'
import { setLocale } from '@/i18n'
import { PENDING_NOTES, usePendingNotes } from '@/lib/pendingNotes'

const stubs = {
  VIcon: true,
  VBtn: { template: '<button v-bind="$attrs"><slot /></button>' },
  BaseButton: { template: '<button v-bind="$attrs"><slot /></button>' },
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

/** 卡上去处下拉里，交给任务芝士那一项写的是什么。 */
async function nameOnCard(): Promise<string> {
  const ui = render(PreviewLocator, {
    props: { target: { label: '第 1 页', quote: '一段原文' }, note: '', canComment: true },
    global: { stubs },
  })
  await fireEvent.click(ui.getByTestId('locator-recipient'))
  const option = ui.getByRole('listbox').querySelector('[role="option"]')
  const label = option?.querySelector('.locator__option-label')?.textContent?.trim()
  if (!label) throw new Error('去处下拉里没有第一项')
  return label
}

/** 待发篮里那一行写的去处。 */
async function nameInTray(): Promise<string> {
  const api = usePendingNotes(() => true)
  api.add({
    file: '招募方案.docx',
    path: 'samples/招募方案.docx',
    version: '',
    where: '第 1 页',
    quote: '报名截止 11 月 8 日',
    text: '把日期改成 11 月 10 日',
    audience: 'cheese',
    message: '',
  })
  const ui = render(PendingNotesTray, { global: { stubs, provide: { [PENDING_NOTES as symbol]: api } } })
  await ui.rerender({})
  return ui.getByTestId('pending-note').textContent ?? ''
}

describe('一个去处的名字', () => {
  it('卡上和待发篮里给同一个去处用的是同一个称呼', async () => {
    const onCard = await nameOnCard()
    const inTray = await nameInTray()
    // 待发篮那一行写的是「待发给任务芝士」这样一句，里面必须含卡上那个称呼本身。
    expect(inTray).toContain(onCard)
  })
})
