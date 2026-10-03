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
const listMaterials = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    update: (...a: unknown[]) => updateSpace(...a),
    listMaterials: (...a: unknown[]) => listMaterials(...a),
  },
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

/** 资料库那一份清单：一条「所有成员」、一条「仅管理员」。 */
const MATERIALS = [
  { id: 161, name: '第03讲-红黑树.pdf', visibility: 'members' },
  { id: 162, name: '参考答案-红黑树.pdf', visibility: 'admins' },
]

function mountPage(teaching?: Record<string, unknown>) {
  listMaterials.mockResolvedValue({ data: { materials: MATERIALS, canManage: true } })
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore(pinia)
  store.setSpace({ id: SPACE_ID, name: '数据结构题板', teaching } as never)
  const utils = render(SpaceGuidance, {
    global: { plugins: [pinia, createVuetify({ components, directives })] },
  })
  return { ...utils, store }
}

/** 摊开「参考资料」那一格里的资料库清单。它默认收起，勾选框点开才在。 */
async function openMaterials(view: ReturnType<typeof mountPage>) {
  await fireEvent.click(view.getByTestId('teaching-materials-toggle'))
}

/** 摊开「高级选项」——周次和另外三格清单都折在里面，默认不在页面上。 */
async function expandAdvanced(view: ReturnType<typeof mountPage>) {
  await fireEvent.click(view.getByText('spaces.teaching.fields.advanced'))
  await waitFor(() => expect(view.getByLabelText('spaces.teaching.fields.currentWeek')).toBeTruthy())
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
  it('这块板存着的那一份填进表单', async () => {
    const view = mountPage({ systemPrompt: '这块板的默认', currentWeek: 3 })

    expect((view.getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement).value).toBe(
      '这块板的默认'
    )

    await expandAdvanced(view)
    expect((view.getByLabelText('spaces.teaching.fields.currentWeek') as HTMLInputElement).value).toBe('3')
  })

  it('保存摆在卡片最后那一条，不在标题下面那条工具行里 —— 和「基本信息」同形', () => {
    mountPage()

    const card = document.querySelector('.settings-card')
    expect(card).toBeTruthy()
    expect(card!.querySelector('.settings-foot')).toBeTruthy()
    // 按钮得在卡片**里面**：摆在卡片外面（标题下那条工具行）是清单页的样子。
    expect(saveButton().closest('.settings-card')).toBe(card)
    expect(document.querySelector('.settings-toolbar')).toBeNull()
  })

  it('点保存把整份发出去 —— 填上的那几格连着空格子一起', async () => {
    updateSpace.mockResolvedValue({ data: { space: { id: SPACE_ID } } })
    const view = mountPage()
    const { getByLabelText } = view

    await fireEvent.update(getByLabelText('spaces.teaching.fields.systemPrompt'), '第 {current_week} 周')
    await expandAdvanced(view)
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

  it('参考资料勾的是这块板资料库里的文件，「仅管理员」那一档不列出来', async () => {
    const view = mountPage()

    await waitFor(() => expect(view.getByTestId('teaching-materials-toggle')).toBeTruthy())
    await openMaterials(view)
    expect(view.getByLabelText('第03讲-红黑树.pdf')).toBeTruthy()
    expect(view.queryByLabelText('参考答案-红黑树.pdf')).toBeNull()

    // Vuetify 的勾选框绑的是 input 的 `input` 事件（`e.target.checked`），点它没用。
    await fireEvent.input(view.getByLabelText('第03讲-红黑树.pdf'), { target: { checked: true } })
    await fireEvent.click(saveButton())

    await waitFor(() => expect(updateSpace).toHaveBeenCalled())
    expect(updateSpace.mock.calls[0][1].teaching.materialIds).toEqual([161])
  })

  it('「用这段默认要求」把那份默认填进框里，不点就不填', async () => {
    const view = mountPage()
    const prompt = view.getByLabelText('spaces.teaching.fields.systemPrompt') as HTMLTextAreaElement

    // 留空就是一条要求都不加：框里是空的，那句话只在灰字里。
    expect(prompt.value).toBe('')

    await fireEvent.click(view.getByTestId('teaching-use-default-template'))
    await waitFor(() => expect(prompt.value).toBe('spaces.teaching.defaultTemplate'))
  })
})
