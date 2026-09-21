// 团队详情第三页：成员列表、加入申请、已发送邀请三个标签页，各有各的空态和文案。
// 三个标签页都必须点进去看——v-window 只渲染当前那一页。
import { ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getMembers: vi.fn(),
  listTeamJoinRequests: vi.fn(),
  listTeamInvitations: vi.fn(),
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ params: { teamId: '1' }, query: {} }),
}))
vi.mock('@/network/api/teams', () => ({
  TeamsApi: {
    getMembers: mocks.getMembers,
    listTeamJoinRequests: mocks.listTeamJoinRequests,
    listTeamInvitations: mocks.listTeamInvitations,
    createInvitation: vi.fn(),
    updateMember: vi.fn(),
    removeMember: vi.fn(),
    approveJoinRequest: vi.fn(),
    rejectJoinRequest: vi.fn(),
    cancelInvitation: vi.fn(),
  },
}))
vi.mock('@/services/account', () => ({ default: { user: { id: 9, nickname: 'Nina' } } }))
vi.mock('@/services/ErrorHandler', () => ({ default: { withErrorHandling: vi.fn(async (fn: () => unknown) => fn()) } }))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import Members from './Members.vue'

import i18n, { setLocale } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const teamData = { id: 1, owner: { id: 9 }, admins: { examples: [{ id: 9 }] } }

const members = [
  { user: { id: 9, nickname: 'Nina', avatarId: null }, role: 'OWNER' },
  { user: { id: 10, nickname: 'Bo', avatarId: null }, role: 'ADMIN' },
  { user: { id: 11, nickname: 'Cy', avatarId: null }, role: 'MEMBER' },
]

const applications = [
  {
    id: 1,
    status: 'PENDING',
    message: 'I would like to help.',
    createdAt: Date.UTC(2024, 0, 2, 3, 4),
    user: { id: 12, nickname: 'Ada', avatarId: null },
  },
  {
    id: 2,
    status: 'APPROVED',
    message: null,
    createdAt: Date.UTC(2024, 0, 3, 5, 6),
    user: { id: 13, nickname: 'Dee', avatarId: null },
    processedBy: { nickname: 'Nina' },
  },
]

const invitations = [
  {
    id: 3,
    status: 'PENDING',
    message: null,
    createdAt: Date.UTC(2024, 0, 4, 7, 8),
    user: { id: 14, nickname: 'Eve', avatarId: null },
  },
]

beforeEach(() => {
  // happy-dom 这两个都不给，而 Vuetify 的浮层定位会真的去读。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  setLocale('zh-CN')
  vi.clearAllMocks()
  mocks.getMembers.mockResolvedValue({ data: { members } })
  mocks.listTeamJoinRequests.mockResolvedValue({ data: { applications } })
  mocks.listTeamInvitations.mockResolvedValue({ data: { invitations } })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mountPage() {
  return render(Members, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      provide: { [teamDataInjectionKey as symbol]: ref(teamData) },
    },
  })
}

/** 三个标签页一次只看得到一个，进下一页得先点。 */
async function openTab(view: ReturnType<typeof mountPage>, name: RegExp) {
  await fireEvent.click(view.getByRole('tab', { name }))
}

it('中文下成员列表标出队长和管理员，三个标签都在', async () => {
  const view = mountPage()

  await view.findByText('Nina')
  expect(view.getByText('队长')).toBeTruthy()
  expect(view.getByText('管理员')).toBeTruthy()
  expect(view.getByRole('tab', { name: /成员列表/ })).toBeTruthy()
  expect(view.getByRole('tab', { name: /加入申请/ })).toBeTruthy()
  expect(view.getByRole('tab', { name: /已发送邀请/ })).toBeTruthy()
})

it('中文下加入申请显示状态、时间和处理人', async () => {
  const view = mountPage()
  await view.findByText('Nina')

  await openTab(view, /加入申请/)

  await view.findByText('Ada')
  expect(view.getByText('待处理')).toBeTruthy()
  expect(view.getByText('已批准')).toBeTruthy()
  // 两条申请各有一行时间，所以是 getAll；冒号后必须还有内容，确认占位符真的被替换了。
  // 不比对具体格式：时间按当前语言格式化，中文是 2024/1/2，英文是 1/2/2024。
  expect(view.getAllByText(/^申请时间: ./).length).toBe(2)
  expect(view.getByText('由 Nina 处理')).toBeTruthy()
  expect(view.getByRole('button', { name: '批准' })).toBeTruthy()
  expect(view.getByRole('button', { name: '拒绝' })).toBeTruthy()
})

it('中文下已发送邀请列出邀请对象和时间', async () => {
  const view = mountPage()
  await view.findByText('Nina')

  await openTab(view, /已发送邀请/)

  await view.findByText('Eve')
  expect(view.getByText(/^邀请时间: /)).toBeTruthy()
})

it('中文下三个标签页各有各的空态', async () => {
  mocks.getMembers.mockResolvedValue({ data: { members: [] } })
  mocks.listTeamJoinRequests.mockResolvedValue({ data: { applications: [] } })
  mocks.listTeamInvitations.mockResolvedValue({ data: { invitations: [] } })
  const view = mountPage()

  await view.findByText('暂无成员')
  expect(view.getByText('邀请成员加入小队，开始协作')).toBeTruthy()

  await openTab(view, /加入申请/)
  await view.findByText('暂无加入申请')
  expect(view.getByText('没有用户申请加入小队')).toBeTruthy()

  await openTab(view, /已发送邀请/)
  await view.findByText('暂无发出的邀请')
  expect(view.getByText('点击右上角的"邀请成员"发送邀请')).toBeTruthy()
})

it('英文下三个标签页都没有汉字', async () => {
  setLocale('en')
  const view = mountPage()

  await view.findByText('Nina')
  expect(view.getByText('Owner')).toBeTruthy()
  expect(view.getByText('Admin')).toBeTruthy()
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)

  await openTab(view, /Requests/)
  await view.findByText('Ada')
  expect(view.getByText('Pending')).toBeTruthy()
  expect(view.getByText('Approved')).toBeTruthy()
  expect(view.getAllByText(/^Requested: ./).length).toBe(2)
  expect(view.getByText('Handled by Nina')).toBeTruthy()
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)

  await openTab(view, /Sent invitations/)
  await view.findByText('Eve')
  expect(view.getByText(/^Invited: /)).toBeTruthy()
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)
})

it('英文下三个标签页的空态和邀请对话框都没有汉字', async () => {
  mocks.getMembers.mockResolvedValue({ data: { members: [] } })
  mocks.listTeamJoinRequests.mockResolvedValue({ data: { applications: [] } })
  mocks.listTeamInvitations.mockResolvedValue({ data: { invitations: [] } })
  setLocale('en')
  const view = mountPage()

  await view.findByText('No members yet')
  expect(view.getByText('Invite people to this team and start collaborating')).toBeTruthy()

  await openTab(view, /Requests/)
  await view.findByText('No requests yet')
  expect(view.getByText('Nobody has asked to join this team')).toBeTruthy()

  await openTab(view, /Sent invitations/)
  await view.findByText('No invitations sent yet')
  expect(view.getByText('Use "Invite a member" in the top right to send one')).toBeTruthy()

  await fireEvent.click(view.getAllByText('Invite a member')[0])
  for (const text of [
    'Invitation message (optional)',
    "Only invitations by UID are supported for now; the UID is in that person's avatar menu",
    'Cancel',
  ]) {
    expect((await view.findAllByText(text)).length).toBeGreaterThan(0)
  }
  expect(CJK.test(document.body.textContent ?? '')).toBe(false)
})
