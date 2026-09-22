// /spaces 是登录后的落点（`/` 把已登录的人送来这里），而这一页通篇是**别人**的
// 空间名录：一个项目都没有的新用户落进来，此前没有任何一行字说他自己的东西从哪
// 儿开，得自己摸到「小队 → 我的 → 小队页 → 项目」。这一份钉的就是顶上那一格：
//   1. 一个项目都没有时出现，且按钮真的打开 App 里那个全局的新建项目对话框
//   2. 有项目的人看到的是和以前一模一样的页面（一格都不多）
//   3. 清单还没回来、或者根本没拿到时，不出现——不拿猜出来的「零」去误导人
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const listProjects = vi.fn()
const spacesList = vi.fn()
const spacesCreate = vi.fn()
const applications = vi.fn()
const uploadAvatar = vi.fn()
const resubmit = vi.fn()
const showNewProjectDialog = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return { ...actual, listProjects: (...a: unknown[]) => listProjects(...a) }
})

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    list: (...a: unknown[]) => spacesList(...a),
    create: (...a: unknown[]) => spacesCreate(...a),
    applications: (...a: unknown[]) => applications(...a),
    resubmit: (...a: unknown[]) => resubmit(...a),
  },
}))

vi.mock('@/network/api/avatars', () => ({
  AvatarsApi: { createAvatar: (...a: unknown[]) => uploadAvatar(...a) },
}))

// 对话框本身住在 App.vue（跨路由活着），这一页只是把它叫起来。
vi.mock('@/composables/useNewProjectDialog', () => ({
  useNewProjectDialog: () => ({ show: showNewProjectDialog }),
}))

// 只换掉取词入口：`@/i18n/index.ts` 会 `createI18n`，整个模块替换掉的话它连
// import 都过不去（`@/api` 那条链会把它拉进来）。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import SpacesIndex from './Index.vue'

import AccountService from '@/services/account'

const START_HERE = '从这里开始'
const NEW_PROJECT = '新建项目'

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mountPage() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'root', component: { template: '<div />' } },
      { path: '/spaces/:spaceId', name: 'SpacesDetail', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/course', name: 'SpacesCourseHome', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: { template: '<div />' } },
    ],
  })
  // PageHeader 要 pinia（它读页面标题那个 store）。
  return { ...render(SpacesIndex, { global: { plugins: [vuetify, router, createPinia()] } }), router }
}

beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
})

beforeEach(() => {
  uploadAvatar.mockReset().mockResolvedValue({ data: { avatarId: 77 } })
  resubmit.mockReset().mockResolvedValue({})
  URL.createObjectURL = vi.fn(() => 'blob:avatar-preview')
  applications.mockReset().mockResolvedValue({ data: { items: [] } })
  listProjects.mockReset()
  spacesList.mockReset().mockResolvedValue({ data: { spaces: [], page: { pageSize: 12, hasMore: false } } })
  showNewProjectDialog.mockReset()
  spacesCreate.mockReset()
  AccountService.loggedIn = true
})

afterEach(cleanup)

describe('space creation', () => {
  it('uploads the selected image before creating the board', async () => {
    listProjects.mockResolvedValue({ data: [] })
    spacesCreate.mockResolvedValue({})
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Practice')
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })
    await fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } })
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)
    await waitFor(() => expect(spacesCreate).toHaveBeenCalledWith({ name: 'Practice', intro: '', avatarId: 77 }))
    expect(uploadAvatar).toHaveBeenCalledWith(file)
  })

  it('keeps the application open when the avatar upload fails', async () => {
    listProjects.mockResolvedValue({ data: [] })
    uploadAvatar.mockRejectedValueOnce(new Error('Upload failed'))
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Practice')
    await fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(['image'], 'avatar.png', { type: 'image/png' })] },
    })
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)
    await waitFor(() => expect(page.getByText('spaces.create.failed')).toBeTruthy())
    expect(spacesCreate).not.toHaveBeenCalled()
    expect((page.getByLabelText('spaces.create.name') as HTMLInputElement).value).toBe('Practice')
  })

  it('lets an ordinary signed-in user submit a space for review', async () => {
    listProjects.mockResolvedValue({ data: [] })
    spacesCreate.mockResolvedValue({ data: { space: { id: 42 } } })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    const submit = page.getByRole('button', { name: 'spaces.create.submit' })
    expect(submit.hasAttribute('disabled')).toBe(true)
    await fireEvent.update(page.getByLabelText('spaces.create.name'), '  Programming course  ')
    await fireEvent.update(page.getByLabelText('spaces.create.intro'), 'Weekly exercises')
    await fireEvent.submit(submit.closest('form')!)
    await waitFor(() => expect(spacesCreate).toHaveBeenCalledTimes(1))
    expect(spacesCreate).toHaveBeenCalledWith({ name: 'Programming course', intro: 'Weekly exercises' })
  })

  it('keeps the draft after a failed request and allows retry', async () => {
    listProjects.mockResolvedValue({ data: [] })
    spacesCreate.mockRejectedValueOnce(new Error('Request failed'))
    spacesCreate.mockResolvedValueOnce({ data: { space: { id: 43 } } })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Course')
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)
    await waitFor(() => expect(page.getByText('spaces.create.failed')).toBeTruthy())
    expect((page.getByLabelText('spaces.create.name') as HTMLInputElement).value).toBe('Course')
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)
    await waitFor(() => expect(spacesCreate).toHaveBeenCalledTimes(2))
  })

  it('says up front that a new board already is a course', async () => {
    listProjects.mockResolvedValue({ data: [] })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()

    // 建版不选模板，建出来就是课程空间——这句话得在提交之前就看得见。
    expect(page.getByText('spaces.create.courseTemplate')).toBeTruthy()
    expect(page.getByText('spaces.create.courseTemplateTag')).toBeTruthy()
  })

  it('lands the creator in the new course instead of back on the list', async () => {
    listProjects.mockResolvedValue({ data: [] })
    // 建出来的题目板就是一门课（服务端说 `isCourse`），所以落点是课程首页。
    spacesCreate.mockResolvedValue({ data: { space: { id: 42, isCourse: true } } })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Programming course')
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)

    await waitFor(() => expect(page.router.currentRoute.value.name).toBe('SpacesCourseHome'))
    expect(page.router.currentRoute.value.params.spaceId).toBe('42')
  })

  it('lands a board that is not a course on the problem list', async () => {
    listProjects.mockResolvedValue({ data: [] })
    spacesCreate.mockResolvedValue({ data: { space: { id: 43 } } })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Not a course')
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)

    await waitFor(() => expect(page.router.currentRoute.value.name).toBe('SpacesDetail'))
    expect(page.router.currentRoute.value.params.spaceId).toBe('43')
  })

  it('hands over the invite code first, then lands in the new board', async () => {
    listProjects.mockResolvedValue({ data: [] })
    spacesCreate.mockResolvedValue({
      data: {
        space: { id: 7, isCourse: true },
        inviteCode: {
          id: 1,
          spaceId: 7,
          code: '9F3A-2C71-B8E4',
          maxUses: 50,
          useCount: 0,
          expiresAt: null,
          createdAt: 0,
        },
      },
    })
    const page = mountPage()
    await fireEvent.click(page.getByRole('button', { name: 'spaces.create.open' }))
    await flush()
    await fireEvent.update(page.getByLabelText('spaces.create.name'), 'Course')
    await fireEvent.submit(page.getByRole('button', { name: 'spaces.create.submit' }).closest('form')!)

    // 码先给他看，人还留在原地。
    await waitFor(() => expect(page.getByText('9F3A-2C71-B8E4')).toBeTruthy())
    expect(page.router.currentRoute.value.name).toBe('root')

    await fireEvent.click(page.getByRole('button', { name: 'spaces.inviteCodes.openCourse' }))
    await waitFor(() => expect(page.router.currentRoute.value.name).toBe('SpacesCourseHome'))
    expect(page.router.currentRoute.value.params.spaceId).toBe('7')
  })

  it('does not offer creation to signed-out visitors', async () => {
    listProjects.mockResolvedValue({ data: [] })
    AccountService.loggedIn = false
    const page = mountPage()
    expect(page.queryByRole('button', { name: 'spaces.create.open' })).toBeNull()
  })
})

describe('题目板名录页的第一次落点', () => {
  it('offers a way in when the visitor owns no project', async () => {
    listProjects.mockResolvedValue({ data: [], total: 0 })
    const { getByText, getByRole } = mountPage()
    await flush()

    expect(getByText(START_HERE)).toBeTruthy()
    // 主按钮就在这一格里，点的是 App 里那个全局对话框——不传 team，
    // `defaultTeamFor` 会挑个人小队。
    getByRole('button', { name: NEW_PROJECT })
    await getByRole('button', { name: NEW_PROJECT }).click()
    expect(showNewProjectDialog).toHaveBeenCalledTimes(1)
    expect(showNewProjectDialog).toHaveBeenCalledWith()
  })

  it('leaves a visitor who already has a project alone', async () => {
    listProjects.mockResolvedValue({
      data: [
        {
          id: 'p1',
          name: '课程材料整理',
          team_id: 7,
          created_at: '2026-09-01T00:00:00Z',
          updated_at: '2026-09-01T00:00:00Z',
        },
      ],
      total: 1,
    })
    const { queryByText, queryAllByRole } = mountPage()
    await flush()

    expect(queryByText(START_HERE)).toBeNull()
    // 以前这一页一个按钮都没有；有项目的人不该凭空多出一个。
    expect(queryAllByRole('button', { name: NEW_PROJECT })).toHaveLength(0)
  })

  it('stays quiet while the project list is unknown', async () => {
    listProjects.mockReturnValue(new Promise(() => {}))
    const { queryByText } = mountPage()
    await flush()

    expect(queryByText(START_HERE)).toBeNull()
  })

  it('stays quiet when the project list cannot be read', async () => {
    listProjects.mockRejectedValue(new Error('加载失败'))
    const { queryByText } = mountPage()
    await flush()

    expect(queryByText(START_HERE)).toBeNull()
  })
})
