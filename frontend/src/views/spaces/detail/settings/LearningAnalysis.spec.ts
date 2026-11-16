// 空间设置里「学情分析」这一栏。守的是四件事：
//   1. 读：开关和知识点清单照服务端回来的摆，读不出来时整块换成错误加重试（不读成「没开」）；
//   2. 写开关：拨一下、点保存，把新值交给 `PATCH .../learning-analysis`；
//   3. 清单：加一条、删一条（删之前先问一句）；
//   4. 预览：点「预览」弹的是成员进空间时看到的那一张告知卡。
//
// 标签一律走 key（`vue-i18n` 的 `useI18n` 在这里被替成 `t(key) => key`），量的是「信交给谁」，
// 不是中文文案。`@/i18n` 那份 `t` 不替（`SaveBar` / `BaseLoadError` 走它）。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const readLearningAnalysis = vi.fn()
const updateLearningAnalysis = vi.fn()
const createKnowledgePoint = vi.fn()
const deleteKnowledgePoint = vi.fn()

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    readLearningAnalysis: (...a: unknown[]) => readLearningAnalysis(...a),
    updateLearningAnalysis: (...a: unknown[]) => updateLearningAnalysis(...a),
    createKnowledgePoint: (...a: unknown[]) => createKnowledgePoint(...a),
    updateKnowledgePoint: vi.fn(),
    deleteKnowledgePoint: (...a: unknown[]) => deleteKnowledgePoint(...a),
    readLearningNotice: vi.fn(),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { spaceId: '11' } }) }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

/** 确认框：测试里直接给答案（默认不删）。 */
const confirmAnswer = vi.fn<(message: string, options?: unknown) => Promise<boolean>>()
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({
    confirm: (message: string, options?: unknown) => ({ wait: () => confirmAnswer(message, options) }),
  }),
}))

import LearningAnalysis from './LearningAnalysis.vue'

// Vuetify 的浮层（那张告知卡）读 `visualViewport` 算位置：happy-dom 里没有。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterAll(() => vi.unstubAllGlobals())

const POINTS = [
  { id: 1, name: '相关子查询', displayOrder: 1 },
  { id: 2, name: '红黑树的旋转', displayOrder: 2 },
]

function reply(data: { enabled: boolean; points?: typeof POINTS; canManage?: boolean } = { enabled: true }) {
  readLearningAnalysis.mockResolvedValue({
    learningAnalysis: { enabled: data.enabled, enabledBy: 1, enabledAt: 1 },
    knowledgePoints: data.points ?? POINTS,
    canManage: data.canManage ?? true,
  })
}

function mountPage() {
  return render(LearningAnalysis, { global: { plugins: [createVuetify({ components, directives })] } })
}

/** 开关本身：Vuetify 把它渲染成一个 `<input type="checkbox">`，id 就是给的 id。 */
function enabledSwitch(): HTMLInputElement {
  const input = document.querySelector('#learning-analysis-enabled')
  expect(input, '没找到开关').toBeTruthy()
  return input as HTMLInputElement
}

/** 按文案找按钮：文案走语言包，被替成了 key。 */
function buttonWith(text: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === text)
  expect(button, `没找到「${text}」`).toBeTruthy()
  return button as HTMLButtonElement
}

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('空间设置：学情分析', () => {
  it('照服务端回来的摆开关和知识点清单', async () => {
    reply({ enabled: true })
    const view = mountPage()

    await waitFor(() => expect(enabledSwitch()).toBeTruthy())
    await waitFor(() => expect(enabledSwitch().checked).toBe(true))
    expect(view.getByText('相关子查询')).toBeTruthy()
    expect(view.getByText('红黑树的旋转')).toBeTruthy()
  })

  it('读不出来时整块换成错误加一条重试，不读成「没开」', async () => {
    readLearningAnalysis.mockRejectedValue(new Error('boom'))
    const view = mountPage()

    await waitFor(() => expect(document.querySelector('[role="alert"]')).toBeTruthy())
    expect(document.querySelector('#learning-analysis-enabled')).toBeNull()

    // 重试：这回读出来了，照着它画（关着的那块板）。重试那颗字的语言包不走替身，
    // 它是 `BaseLoadError` 从 `@/i18n` 拿的，所以按角色找那一块里唯一的按钮。
    reply({ enabled: false, points: [] })
    const retry = document.querySelector('[role="alert"] button')
    expect(retry, '重试那颗不该缺').toBeTruthy()
    await fireEvent.click(retry as HTMLButtonElement)
    await waitFor(() => expect(enabledSwitch()).toBeTruthy())
    expect(enabledSwitch().checked).toBe(false)
    expect(view.queryByText('相关子查询')).toBeNull()
  })

  it('拨一下开关、点保存，把新值交给 PATCH', async () => {
    reply({ enabled: true })
    updateLearningAnalysis.mockResolvedValue({ learningAnalysis: { enabled: false, enabledBy: 1, enabledAt: 1 } })
    mountPage()

    await waitFor(() => expect(enabledSwitch().checked).toBe(true))
    // Vuetify 的开关读 `input` 事件，和人点一下产生的那个一样。
    await fireEvent.input(enabledSwitch(), { target: { checked: false } })

    // 有改动那条才展开，保存那颗才在。
    await waitFor(() => expect(buttonWith('spaces.learningAnalysis.save')).toBeTruthy())
    await fireEvent.click(buttonWith('spaces.learningAnalysis.save'))

    await waitFor(() => expect(updateLearningAnalysis).toHaveBeenCalledWith(11, { enabled: false }))
  })

  it('新建知识点：填个名字，交回容器去加', async () => {
    reply({ enabled: true })
    createKnowledgePoint.mockResolvedValue({ knowledgePoint: { id: 3, name: '递归', displayOrder: 3 } })
    const view = mountPage()

    await waitFor(() => expect(view.getByTestId('learning-point-add')).toBeTruthy())
    await fireEvent.click(view.getByTestId('learning-point-add'))
    await waitFor(() => expect(document.querySelector('.v-dialog')).toBeTruthy())

    await fireEvent.update(view.getByPlaceholderText('spaces.learningAnalysis.knowledgePoint.namePlaceholder'), '递归')
    await fireEvent.click(buttonWith('spaces.learningAnalysis.knowledgePoint.save'))

    await waitFor(() => expect(createKnowledgePoint).toHaveBeenCalledWith(11, { name: '递归' }))
  })

  it('删一条先问一句，确认了才交回容器去删', async () => {
    reply({ enabled: true })
    deleteKnowledgePoint.mockResolvedValue(undefined)
    confirmAnswer.mockResolvedValue(true)
    const view = mountPage()

    await waitFor(() => expect(view.getByText('相关子查询')).toBeTruthy())
    const row = view.getByText('相关子查询').closest('.v-list-item') as HTMLElement
    // 行末两颗是纯图标按钮：先改名字，后删。
    const buttons = Array.from(row.querySelectorAll('button'))
    expect(buttons.length).toBe(2)
    await fireEvent.click(buttons[1])

    await waitFor(() => expect(deleteKnowledgePoint).toHaveBeenCalledWith(11, 1))
  })

  it('不愿删就不删', async () => {
    reply({ enabled: true })
    confirmAnswer.mockResolvedValue(false)
    const view = mountPage()

    await waitFor(() => expect(view.getByText('相关子查询')).toBeTruthy())
    const row = view.getByText('相关子查询').closest('.v-list-item') as HTMLElement
    await fireEvent.click(Array.from(row.querySelectorAll('button'))[1])

    await waitFor(() => expect(confirmAnswer).toHaveBeenCalled())
    expect(deleteKnowledgePoint).not.toHaveBeenCalled()
  })

  it('点「预览」弹的是成员进空间看到的那一张告知卡', async () => {
    reply({ enabled: true })
    const view = mountPage()

    await waitFor(() => expect(view.getByTestId('learning-notice-preview')).toBeTruthy())
    await fireEvent.click(view.getByTestId('learning-notice-preview'))

    await waitFor(() => expect(view.getByTestId('learning-notice')).toBeTruthy())
    expect(view.getByTestId('learning-notice').querySelector('.learning-notice__title')).toBeTruthy()
  })
})
