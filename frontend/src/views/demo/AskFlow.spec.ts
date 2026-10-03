// 提问流程的**链路验收**：不是「挂得起来」，是把链路真的一条条走通。
//
// `catalog.spec.ts` 那些测的是「每一格渲染出来不报错、不报警告」——那不算链路验过。
// 这一份每条用例都**动手**：点选项、打字、提交、看失败、重试、稍后、回来。
//
// 走通的链路：
//   1. 自由输入真的进答案
//   2. 多题逐步提交
//   3. 提交被服务拒掉后输入还在
//   4. 重试一次就成功
//   5. 稍后处理找得回来
//   6. 卸载重挂后草稿还在（**弱于浏览器刷新**，真正的刷新见 `ask-ux-preview/reload-proof.mjs`）
//   7. 载荷按预定契约来，不是「组件吐什么就认什么」
//   8. 键盘不抢中文输入法和文本框
//
// **这是真组件 + 假服务 + 假数据**，不是真实产品链路 —— 产品还没有这条链路。
// 失败/重试那两条走的是 `fakeAnswerService`（真的被调、真的被拒），但服务是假的。
// 这一句是这份测试的边界，报告里也这么写。
//
// 用 `fireEvent` 不用 `userEvent`：本仓库只装了 `@testing-library/vue`，交互那几件
// 事（`TransferProjectDialog.spec.ts` 等）也都走它，不为了这一份多引一个依赖。
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AskFlow, { type AskQuestion } from './AskFlow.vue'
import { fakeAnswerService } from './fakeAnswerService'

const Q: AskQuestion[] = [
  {
    id: 'data',
    header: '数据用哪份',
    question: '这次跑演示，数据用哪一份？',
    options: [
      { label: '课程发的 sales.csv', description: '会覆盖项目里 3 处引用' },
      { label: '项目里的演示数据', description: '不动任何引用，只是这次先用它' },
    ],
  },
  {
    id: 'env',
    header: '跑在哪',
    question: '演示跑在哪台机器上？',
    options: [
      { label: '我这台开发机', description: '快，但你走开就断了' },
      { label: '云端那台', description: '慢一点，一直开着' },
    ],
  },
]

/**
 * `emitted().submit` 是「每次 emit 一层参数元组」，落出来是 `[[载荷], [载荷]]`。
 * 这里只把载荷摊平，免得每条用例都写 `emitted().submit[i][0]`。
 */
function payloads<T>(scope: ReturnType<typeof open>, name: string): T[] {
  return ((scope.emitted()[name] as unknown[][]) ?? []).map((args) => args[0] as T)
}

function open(props: Record<string, unknown> = {}) {
  return render(AskFlow, {
    props: { questions: Q, answeredBy: '王长鑫', ...props },
  })
}

/** 备注框。placeholder 是中文长句，按前缀认。 */
function noteBox(scope: ReturnType<typeof open>) {
  return scope.getByPlaceholderText(/或者写一句别的/) as HTMLInputElement
}

/**
 * 按「提交回答」，并走完「还有没答的，照样交吗」那一步。
 *
 * 两件待拍板的事只答一件就按提交，出来的**不是提交**而是那一句确认 —— 这正是
 * Codex 的「Submit with N unanswered questions?」。链路测试要走的是完整这条路，
 * 所以这里把「照样交」一起按掉，别当成提交失败。
 */
async function submitThrough(scope: ReturnType<typeof open>) {
  await fireEvent.click(scope.getByText('提交回答'))
  const proceed = scope.queryByText('照样交')
  if (proceed) await fireEvent.click(proceed)
  // 提交是异步的（真的等服务回话），把宏任务让完再往下断言。
  await new Promise((r) => setTimeout(r, 0))
}

describe('提问流程的链路', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('1. 自由输入真的进答案：备注跟着这道题交出去', async () => {
    const scope = open()

    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await fireEvent.update(noteBox(scope), '先用演示数据')
    await submitThrough(scope)

    const submits = payloads<{ option: string; note: string }>(scope, 'submit')
    expect(submits).toHaveLength(1)
    expect(submits[0]).toMatchObject({
      option: '项目里的演示数据',
      note: '先用演示数据',
    })
  })

  it('2. 多题逐步提交：交完第一件，进度跟上，第二件照样能答', async () => {
    const scope = open()

    // 第一件：进度 0/2，题目是第一件的。
    expect(scope.getByText('0/2 已答')).toBeTruthy()
    expect(scope.getByText(/这次跑演示，数据用哪一份/)).toBeTruthy()

    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await submitThrough(scope)

    expect(scope.getByText('1/2 已答')).toBeTruthy()
    // 第一件回执出来了。
    expect(scope.getByText(/选了「项目里的演示数据」/)).toBeTruthy()

    // 切到第二件答掉。
    await fireEvent.click(scope.getByText('跑在哪', { selector: '.flow__tab-label' }))
    await fireEvent.click(scope.getByText('云端那台'))
    await submitThrough(scope)

    expect(scope.getByText('2/2 已答')).toBeTruthy()
    const submits = payloads<{ id: string }>(scope, 'submit')
    expect(submits.map((s) => s.id)).toEqual(['data', 'env'])
  })

  it('3. 提交被服务拒掉后输入还在：不吞掉已经选好的和写下的', async () => {
    const svc = fakeAnswerService({ failFirst: 9 })
    const scope = open({ submitAnswer: svc.submit })

    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await fireEvent.update(noteBox(scope), '别覆盖')
    await submitThrough(scope)

    // 失败要看得见。
    expect(scope.getByText(/没答上/)).toBeTruthy()
    // **真的打到过服务**，不是组件自己编的失败。
    expect(svc.calls()).toBe(1)
    expect(svc.log[0]).toMatchObject({ option: '项目里的演示数据', note: '别覆盖' })
    // 一次都没记成答。
    expect(payloads(scope, 'submit')).toHaveLength(0)

    // 输入原样还在。
    expect(noteBox(scope).value).toBe('别覆盖')
  })

  it('4. 重试一次就成功：失败之后再交一次就交上去了', async () => {
    const svc = fakeAnswerService({ failFirst: 1 })
    const scope = open({ submitAnswer: svc.submit })

    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await fireEvent.update(noteBox(scope), '别覆盖')
    await submitThrough(scope)
    expect(scope.getByText(/没答上/)).toBeTruthy()
    expect(svc.calls()).toBe(1)

    await fireEvent.click(scope.getByText('再交一次'))
    await new Promise((r) => setTimeout(r, 0))

    expect(scope.queryByText(/没答上/)).toBeNull()
    expect(scope.getByText(/选了「项目里的演示数据」/)).toBeTruthy()
    // 第二次真的也打到服务了，服务放行。
    expect(svc.calls()).toBe(2)
    const submits = payloads<{ option: string; note: string }>(scope, 'submit')
    expect(submits).toHaveLength(1)
    // 失败时没丢的东西，重试交上去的是完整的。
    expect(submits[0]).toMatchObject({ option: '项目里的演示数据', note: '别覆盖' })
  })

  it('5. 稍后处理找得回来：放着不等于忘了', async () => {
    const scope = open()

    await fireEvent.click(scope.getByText('稍后处理'))

    expect(scope.getByText(/先放着了/)).toBeTruthy()
    expect(payloads(scope, 'defer')).toHaveLength(1)

    // 回来点开就能答。
    await fireEvent.click(scope.getByText('现在答'))
    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await submitThrough(scope)
    expect(scope.getByText(/选了「项目里的演示数据」/)).toBeTruthy()
  })

  it('6. 卸载重挂后草稿还在（弱于浏览器刷新）', async () => {
    const first = open({ persistKey: 'spec-6' })

    await fireEvent.click(first.getByText('项目里的演示数据'))
    await fireEvent.update(noteBox(first), '先用演示数据，等老师确认了再换')
    // 不提交 —— 模拟关掉这个组件。
    first.unmount()

    const second = open({ persistKey: 'spec-6' })
    expect(noteBox(second).value).toBe('先用演示数据，等老师确认了再换')
    // 选中也回来了，而且**没当成已经答了**。
    expect(second.getByText('草稿没交')).toBeTruthy()
    expect(second.getByText('0/2 已答')).toBeTruthy()
  })

  it('6b. 卸载重挂后已交的也算数，不会要人重答一遍', async () => {
    const first = open({ persistKey: 'spec-6b' })

    await fireEvent.click(first.getByText('项目里的演示数据'))
    await submitThrough(first)
    first.unmount()

    const second = open({ persistKey: 'spec-6b' })
    expect(second.getByText('1/2 已答')).toBeTruthy()
    expect(second.getByText(/选了「项目里的演示数据」/)).toBeTruthy()
  })

  /**
   * 这条对着**预定契约**断言，不是对着组件的形状断言。
   *
   * 契约来自 `backend/app/api/routes/topics.py` 的 `answer_options`：`option` 要逐字
   * 命中 `meta.options`，`author` 不能空。所以这里的断言是「交出去的是不是能被那个
   * 端点认下的东西」，组件改成别的形状就得跟着改产品，不能反过来迁就组件。
   */
  it('7. 载荷按预定契约来：option 是被选中那一项的 label，不是下标或别的什么', async () => {
    const svc = fakeAnswerService({ failFirst: 0 })
    const scope = open({ submitAnswer: svc.submit })

    await fireEvent.click(scope.getByText('课程发的 sales.csv'))
    await fireEvent.update(noteBox(scope), '覆盖就覆盖')
    await submitThrough(scope)

    const got = svc.log[0]
    expect(got.id).toBe('data')
    // 逐字是那一项的 label —— 现端点拿这一串去比 `meta.options`。
    expect(got.option).toBe('课程发的 sales.csv')
    // 不是选项的说明文字（那是给人看的「选了会怎样」，不是答案本身）。
    expect(got.option).not.toContain('覆盖项目里 3 处引用')
    // 自由输入原文进 note —— **这是拟新增字段，现端点收不到**，见 fakeAnswerService。
    expect(got.note).toBe('覆盖就覆盖')
    // option 不能是空的：现端点空 option 直接 400。
    expect(got.option.trim()).not.toBe('')
  })
})

describe('键盘不抢中文输入和打字', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('焦点在备注框里时，j / k / 数字是打字，不是选项快捷键', async () => {
    const scope = open()
    const note = noteBox(scope)

    await fireEvent.click(note)
    await fireEvent.update(note, 'jk12')
    await fireEvent.keyDown(note, { key: 'j' })
    await fireEvent.keyDown(note, { key: 'k' })
    await fireEvent.keyDown(note, { key: '1' })

    // 这几个键要是被抢走，note 里的字会被当成操作、选项会被选中。
    expect(note.value).toBe('jk12')
  })

  it('输入法组字中的回车不提交（isComposing）', async () => {
    const scope = open()

    await fireEvent.click(noteBox(scope))
    await fireEvent.update(noteBox(scope), '先用')
    // 组字中按下的 Enter：`isComposing` 为真，不该当成「提交」。
    await fireEvent.keyDown(noteBox(scope), { key: 'Enter', isComposing: true })

    expect(payloads(scope, 'submit')).toHaveLength(0)
  })

  it('没选也没写，回车交不出去', async () => {
    const scope = open()

    await fireEvent.keyDown(scope.container.querySelector('.flow')!, { key: 'Enter' })

    expect(payloads(scope, 'submit')).toHaveLength(0)
    expect(scope.getByText('先选一项，或者写一句')).toBeTruthy()
  })

  it('选了之后回车交得出去', async () => {
    const scope = open({ initialConfirm: true })

    await fireEvent.click(scope.getByText('项目里的演示数据'))
    await fireEvent.keyDown(scope.container.querySelector('.flow')!, { key: 'Enter' })
    await new Promise((r) => setTimeout(r, 0))

    const submits = payloads<{ option: string }>(scope, 'submit')
    expect(submits).toHaveLength(1)
    expect(submits[0].option).toBe('项目里的演示数据')
  })
})
