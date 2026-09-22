// 课程参数屏钉三件事：
//   1. 只画**今天有界面在听**的模块开关（拨了没反应的开关比没有开关更糟）；
//   2. 保存开关时发的是**整张表**，且把还没接上界面的那几格原样带上 —— 否则这一屏
//      会顺手擦掉别人写过的值；
//   3. 教学参数读写的是**默认分组**上那份 `teaching`，整份替换。
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spacesDetail = vi.fn()
const listCategories = vi.fn()
const updateSpace = vi.fn()
const updateCategory = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    update: (...a: unknown[]) => updateSpace(...a),
    updateCategory: (...a: unknown[]) => updateCategory(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import CourseSettings from './CourseSettings.vue'

const SPACE_ID = 11
const CATEGORY_ID = 21

/** 一门课：默认分组声明了课程壳（服务端算成 `isCourse`），已经有一个模块被关掉。 */
const COURSE = {
  id: SPACE_ID,
  intro: '',
  name: '程序设计基础',
  avatarId: 1,
  admins: [{ user: { id: 4 }, role: 'OWNER' }],
  announcements: '[]',
  taskTemplates: '[]',
  classificationTopics: [],
  defaultCategoryId: CATEGORY_ID,
  isCourse: true,
  courseModules: { team: false, quiz: false },
}

const CATEGORY = {
  id: CATEGORY_ID,
  name: '作业',
  description: null,
  displayOrder: 0,
  createdAt: 0,
  updatedAt: 0,
  archivedAt: null,
  teaching: {
    systemPrompt: '只讲本周的东西',
    currentWeek: 3,
    allowedTopics: ['循环', '数组'],
    avoidInCode: ['递归'],
    materialIds: [7],
    knowledgeIds: [],
  },
}

async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div></div>' } },
      { path: '/spaces/:spaceId/course/settings', name: 'settings', component: { template: '<div></div>' } },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/course/settings`)
  await router.isReady()
  return render(CourseSettings, {
    global: {
      plugins: [createPinia(), createVuetify({ components, directives }), router],
    },
  })
}

describe('CourseSettings', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    spacesDetail.mockResolvedValue({ data: { space: COURSE } })
    listCategories.mockResolvedValue({ data: { categories: [CATEGORY] } })
    updateSpace.mockResolvedValue({
      data: { space: { ...COURSE, courseModules: { team: false, quiz: false, units: false } } },
    })
    updateCategory.mockResolvedValue({ data: {} })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('draws only the modules that have a screen to switch', async () => {
    const { container } = await mountPage()
    await waitFor(() => expect(container.querySelectorAll('.v-switch').length).toBe(4))
  })

  it('sends every switch it draws, and leaves the ones it does not draw alone', async () => {
    const { container } = await mountPage()
    await waitFor(() => expect(container.querySelectorAll('.v-switch').length).toBe(4))

    // 关掉第一个（教学单元），保存。用原生 `click()` 而不是 `fireEvent.click`：后者
    // 只派发一个 click 事件，而 jsdom 的复选框是在**默认行为**里才补发 input/change，
    // Vuetify 听的正是 change。
    const firstSwitch = container.querySelector('.v-switch input') as HTMLInputElement
    firstSwitch.click()
    await nextTick()
    await fireEvent.click([...container.querySelectorAll('button')].find((b) => b.textContent?.includes('save'))!)

    await waitFor(() => expect(updateSpace).toHaveBeenCalled())
    const [, payload] = updateSpace.mock.calls[0]
    // 画了的三格都在；`quiz: false` 这一屏没画它，但它**必须**原样还在 —— 这一屏
    // 不该擦掉别人写过的值（也正因此 `quiz` 不能因为「没写」而被抹成缺省的 true）。
    expect(payload.courseModules).toEqual({
      team: false,
      quiz: false,
      units: false,
      assignments: true,
      stuck: true,
    })
  })

  it('reads the teaching config off the default grouping', async () => {
    const { container } = await mountPage()
    const values = () =>
      [...container.querySelectorAll('input, textarea')].map(
        (el) => (el as HTMLInputElement | HTMLTextAreaElement).value
      )
    // 参数是异步取回来的（space + 默认分组），要等它落到表单上再断言。
    await waitFor(() => expect(values()).toContain('3'))
    expect(values()).toContain('循环, 数组')
    expect(values()).toContain('递归')
    expect(values()).toContain('只讲本周的东西')
  })

  it('writes the whole teaching config back, on the course grouping', async () => {
    const { container } = await mountPage()
    const weekOf = () => [...container.querySelectorAll('input')].find((el) => (el as HTMLInputElement).value === '3')
    await waitFor(() => expect(weekOf()).toBeTruthy())

    await fireEvent.update(weekOf()!, '4')
    const saves = [...container.querySelectorAll('button')].filter((b) =>
      b.textContent?.includes('spaces.course.settings.save')
    )
    await fireEvent.click(saves[saves.length - 1])

    await waitFor(() => expect(updateCategory).toHaveBeenCalled())
    const [spaceId, categoryId, payload] = updateCategory.mock.calls[0]
    expect(spaceId).toBe(SPACE_ID)
    expect(categoryId).toBe(CATEGORY_ID)
    expect(payload.teaching).toEqual({
      systemPrompt: '只讲本周的东西',
      currentWeek: 4,
      allowedTopics: ['循环', '数组'],
      avoidInCode: ['递归'],
      materialIds: [7],
      knowledgeIds: [],
    })
  })
})
