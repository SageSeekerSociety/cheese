// 空间设置里「给 AI 队友的指导」那一栏（#944）。这一栏读写都搭在已有的实体接口上
// （`PATCH /spaces/{id}`），所以量的是三件事：
//   1. 读：这块板存着的那一份填进表单；
//   2. 写：点保存把**这份默认**整份发出去；六格全空 = 清掉它；
//   3. 存不上时表单留着，人填的内容不丢。
//
// 标签一律走 key（i18n 在下面被替成一个 `t(key) => key`），量的是「信交给谁」，
// 不是中文文案本身。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

const updateSpace = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { update: (...a: unknown[]) => updateSpace(...a) },
}))

vi.mock('@/network/api/tasks', () => ({ TasksApi: { list: vi.fn() } }))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import SpaceGuidance from './SpaceGuidance.vue'

import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11

function mountPage(teaching?: Record<string, unknown>) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore(pinia)
  store.setSpace({ id: SPACE_ID, name: '数据结构题板', teaching } as never)
  const utils = render(SpaceGuidance, {
    global: { plugins: [pinia, createVuetify({ components, directives })] },
  })
  return { ...utils, store }
}

/** 保存那颗按钮。`v-expansion-panel` 的开关也是一颗 button，所以按文案精确挑。 */
function saveButton() {
  const button = Array.from(document.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === 'spaces.guidance.save'
  )
  expect(button, '没找到保存按钮').toBeTruthy()
  return button as HTMLButtonElement
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('空间设置：给 AI 队友的指导', () => {
  it('这块板存着的那一份填进表单', () => {
    const { getByLabelText } = mountPage({ systemPrompt: '这块板的默认', currentWeek: 3 })

    expect((getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement).value).toBe('这块板的默认')
    expect((getByLabelText('spaces.teaching.fields.currentWeek') as HTMLInputElement).value).toBe('3')
  })

  it('点保存把整份发出去 —— 填上的那几格连着空格子一起', async () => {
    updateSpace.mockResolvedValue({ data: { space: { id: SPACE_ID } } })
    const { getByLabelText } = mountPage()

    await fireEvent.update(getByLabelText('spaces.teaching.fields.systemPrompt'), '第 {current_week} 周')
    await fireEvent.update(getByLabelText('spaces.teaching.fields.currentWeek'), '3')
    await fireEvent.click(saveButton())

    await waitFor(() =>
      expect(updateSpace).toHaveBeenCalledWith(SPACE_ID, {
        teaching: {
          systemPrompt: '第 {current_week} 周',
          currentWeek: 3,
          allowedTopics: [],
          avoidInCode: [],
          materialIds: [],
          knowledgeIds: [],
        },
      })
    )
  })

  it('六格全空 = 清掉这块板的默认（照样发一份空的上去）', async () => {
    updateSpace.mockResolvedValue({ data: { space: { id: SPACE_ID } } })
    const { getByLabelText } = mountPage({ systemPrompt: '从前的默认' })

    await fireEvent.update(getByLabelText('spaces.teaching.fields.systemPrompt'), '')
    await fireEvent.click(saveButton())

    await waitFor(() => expect(updateSpace).toHaveBeenCalledTimes(1))
    expect(updateSpace.mock.calls[0][1]).toEqual({
      teaching: {
        systemPrompt: null,
        currentWeek: null,
        allowedTopics: [],
        avoidInCode: [],
        materialIds: [],
        knowledgeIds: [],
      },
    })
  })

  it('存不上时表单留着，填的内容不丢', async () => {
    updateSpace.mockRejectedValue(new Error('boom'))
    const { getByLabelText } = mountPage()

    const input = getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement
    await fireEvent.update(input, '还没存上的这一份')
    await fireEvent.click(saveButton())

    await waitFor(() => expect(updateSpace).toHaveBeenCalled())
    expect(input.value).toBe('还没存上的这一份')
  })
})
