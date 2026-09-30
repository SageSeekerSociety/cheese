// 题目板取数那一层的规矩：发不发、发什么、失败了怎么办。
//
// 这一层原先长在 `stores/space.ts` 里（store 自己 import 接口），现在长在
// `composables/useSpaceData.ts`，store 只剩状态。钉在这一层是因为下面这几条都是
// 「换一块板之后还看不看得见上一块的东西」「删了之后列表是不是真的重拉了一遍」这
// 一类问题 —— 屏幕上不一定看得见，但都能从请求上判对错。
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const spaceUpdate = vi.fn()
const listCategories = vi.fn()
const listDomainGroups = vi.fn()
const createCategory = vi.fn()
const updateCategory = vi.fn()
const deleteCategory = vi.fn()
const archiveCategory = vi.fn()
const unarchiveCategory = vi.fn()
const addAdmin = vi.fn()
const updateAdmin = vi.fn()
const removeAdmin = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    update: (...a: unknown[]) => spaceUpdate(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    listDomainGroups: (...a: unknown[]) => listDomainGroups(...a),
    createCategory: (...a: unknown[]) => createCategory(...a),
    updateCategory: (...a: unknown[]) => updateCategory(...a),
    deleteCategory: (...a: unknown[]) => deleteCategory(...a),
    archiveCategory: (...a: unknown[]) => archiveCategory(...a),
    unarchiveCategory: (...a: unknown[]) => unarchiveCategory(...a),
    addAdmin: (...a: unknown[]) => addAdmin(...a),
    updateAdmin: (...a: unknown[]) => updateAdmin(...a),
    removeAdmin: (...a: unknown[]) => removeAdmin(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import { toast } from 'vuetify-sonner'

import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 647

/** 被测的那一层：取数。 */
function data() {
  return useSpaceData()
}

/** 它写进去的地方：状态。 */
function state() {
  return useSpaceStore()
}

function space(id = SPACE_ID, overrides: Record<string, unknown> = {}) {
  return {
    id,
    name: '数据结构空间',
    intro: '',
    avatarId: null,
    admins: [{ user: { id: 4, nickname: '管理员' }, role: 'OWNER' }],
    announcements: '[]',
    taskTemplates: '[]',
    classificationTopics: [],
    visibleTaskLimit: null,
    ...overrides,
  }
}

function category(id: number, name = `分类 ${id}`) {
  return { id, name, description: null, displayOrder: id, archivedAt: null }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(console, 'error').mockImplementation(() => {})
  spaceDetail.mockReset().mockImplementation(async (id: number) => ({ data: { space: space(id) } }))
  spaceUpdate.mockReset().mockImplementation(async (id: number) => ({ data: { space: space(id) } }))
  listCategories
    .mockReset()
    .mockImplementation(async () => ({ data: { categories: [category(1), category(2)] } }))
  listDomainGroups.mockReset().mockImplementation(async () => ({ data: { groups: [] } }))
  createCategory.mockReset().mockResolvedValue({ data: {} })
  updateCategory.mockReset().mockResolvedValue({ data: {} })
  deleteCategory.mockReset().mockResolvedValue({ data: {} })
  archiveCategory.mockReset().mockResolvedValue({ data: {} })
  unarchiveCategory.mockReset().mockResolvedValue({ data: {} })
  addAdmin.mockReset().mockImplementation(async (id: number) => ({ data: { space: space(id) } }))
  updateAdmin.mockReset().mockImplementation(async (id: number) => ({ data: { space: space(id) } }))
  removeAdmin.mockReset().mockResolvedValue({ data: {} })
  vi.mocked(toast.error).mockClear()
  vi.mocked(toast.success).mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('fetchSpace', () => {
  it('取板的时候连着分类话题和我的排名一起要 —— 页头那两处读的是它们', async () => {
    await data().fetchSpace(SPACE_ID)

    expect(spaceDetail).toHaveBeenCalledWith(SPACE_ID, {
      queryClassificationTopics: true,
      queryMyRank: true,
    })
    expect(state().currentSpace?.id).toBe(SPACE_ID)
    expect(state().currentSpaceId).toBe(SPACE_ID)
  })

  it('换一块板：上一块的正文和分类先清掉，免得新板还没回来时屏幕上留着旧的', async () => {
    await data().fetchSpace(SPACE_ID)
    const before = state().currentSpace

    let release: (value: unknown) => void = () => {}
    spaceDetail.mockImplementation(
      () => new Promise((resolve) => (release = resolve)) as Promise<never>
    )
    const pending = data().fetchSpace(SPACE_ID + 1)

    expect(state().currentSpace).toBeNull()
    expect(state().categories).toEqual([])
    expect(state().currentSpaceId).toBe(SPACE_ID + 1)

    release({ data: { space: space(SPACE_ID + 1) } })
    await pending
    expect(state().currentSpace?.id).toBe(SPACE_ID + 1)
    expect(before?.id).toBe(SPACE_ID)
  })

  it('同一块板再取一次不清空：刷新不该让正文闪一下', async () => {
    await data().fetchSpace(SPACE_ID)
    let release: (value: unknown) => void = () => {}
    spaceDetail.mockImplementation(() => new Promise((resolve) => (release = resolve)) as Promise<never>)

    const pending = data().fetchSpace(SPACE_ID)
    expect(state().currentSpace?.id).toBe(SPACE_ID)

    release({ data: { space: space(SPACE_ID) } })
    await pending
  })

  it('取不到就报一句话，不往外抛 —— 调用方是页面，不是能处理这个错的地方', async () => {
    spaceDetail.mockRejectedValue(new Error('500'))

    await expect(data().fetchSpace(SPACE_ID)).resolves.toBeUndefined()
    expect(toast.error).toHaveBeenCalledWith('获取空间信息失败')
  })
})

describe('fetchCategories', () => {
  it('没有板可读时一个请求都不发', async () => {
    await data().fetchCategories()

    expect(listCategories).not.toHaveBeenCalled()
  })

  it('默认只列没归档的；要归档的一起列是调用方说了算', async () => {
    await data().fetchSpace(SPACE_ID)
    await data().fetchCategories()
    expect(listCategories).toHaveBeenLastCalledWith(SPACE_ID, { includeArchived: false })

    await data().fetchCategories(true)
    expect(listCategories).toHaveBeenLastCalledWith(SPACE_ID, { includeArchived: true })
    expect(state().categories.map((c) => c.id)).toEqual([1, 2])
  })

  it('请求在飞的这段时间 loading 是开的，回来之后关掉', async () => {
    await data().fetchSpace(SPACE_ID)
    let release: (value: unknown) => void = () => {}
    listCategories.mockImplementation(
      () => new Promise((resolve) => (release = resolve)) as Promise<never>
    )

    const pending = data().fetchCategories()
    expect(state().loadingCategories).toBe(true)

    release({ data: { categories: [] } })
    await pending
    expect(state().loadingCategories).toBe(false)
  })

  it('失败也要把 loading 关掉，否则骨架屏永远转下去', async () => {
    await data().fetchSpace(SPACE_ID)
    listCategories.mockRejectedValue(new Error('500'))

    await data().fetchCategories()

    expect(state().loadingCategories).toBe(false)
    expect(toast.error).toHaveBeenCalledWith('获取分类失败')
  })
})

describe('fetchDomainGroups', () => {
  it('先看调用方给的板号，没给才用当前这块', async () => {
    await data().fetchDomainGroups(42)
    expect(listDomainGroups).toHaveBeenLastCalledWith(42)

    await data().fetchSpace(SPACE_ID)
    await data().fetchDomainGroups()
    expect(listDomainGroups).toHaveBeenLastCalledWith(SPACE_ID)
  })

  it('一块板都不知道是谁的时候不发请求', async () => {
    await data().fetchDomainGroups()

    expect(listDomainGroups).not.toHaveBeenCalled()
  })

  it('返回 null 当空列表存，不是 null', async () => {
    listDomainGroups.mockResolvedValue({ data: { groups: null } })

    await data().fetchDomainGroups(SPACE_ID)

    expect(state().domainGroups).toEqual([])
  })

  it('失败了留着上一次那几组，只记一句', async () => {
    listDomainGroups.mockResolvedValue({ data: { groups: [{ id: 1 }] } })
    await data().fetchDomainGroups(SPACE_ID)

    listDomainGroups.mockRejectedValue(new Error('500'))
    await data().fetchDomainGroups(SPACE_ID)

    expect(state().domainGroups).toEqual([{ id: 1 }])
  })
})

describe('分类的增删改', () => {
  beforeEach(async () => {
    await data().fetchSpace(SPACE_ID)
    listCategories.mockClear()
  })

  it('改完重新拉一遍，不是本地换一个格子 —— 服务端排出来的顺序和归档状态只有它知道', async () => {
    await data().updateCategory(7, { name: '新名字' })

    expect(updateCategory).toHaveBeenCalledWith(SPACE_ID, 7, { name: '新名字' })
    expect(listCategories).toHaveBeenCalledTimes(1)
  })

  it('新建、删除、归档、恢复各走各的接口，之后都重新拉一遍', async () => {
    await data().createCategory('新课', '说明', 3)
    expect(createCategory).toHaveBeenCalledWith(SPACE_ID, {
      name: '新课',
      description: '说明',
      displayOrder: 3,
    })

    await data().deleteCategory(7)
    expect(deleteCategory).toHaveBeenCalledWith(SPACE_ID, 7)

    await data().archiveCategory(8)
    expect(archiveCategory).toHaveBeenCalledWith(SPACE_ID, 8)

    await data().unarchiveCategory(9)
    expect(unarchiveCategory).toHaveBeenCalledWith(SPACE_ID, 9)

    expect(listCategories).toHaveBeenCalledTimes(4)
  })

  it('写失败往外抛：页面要拿这个错决定弹窗关不关', async () => {
    createCategory.mockRejectedValue(new Error('403'))

    await expect(data().createCategory('没权限')).rejects.toThrow('403')
    expect(toast.error).toHaveBeenCalledWith('创建分类失败')
  })

  it('设默认分类是改板本身，不是改分类', async () => {
    await data().setDefaultCategory(7)

    expect(spaceUpdate).toHaveBeenCalledWith(SPACE_ID, { defaultCategoryId: 7 })
    expect(listCategories).not.toHaveBeenCalled()
  })
})

describe('updateSpace', () => {
  it('把服务端回来的那版整个换进去，提示一句话', async () => {
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockResolvedValue({ data: { space: space(SPACE_ID, { name: '改过的名字' }) } })

    await data().updateSpace(SPACE_ID, { name: '改过的名字' })

    expect(state().currentSpace?.name).toBe('改过的名字')
    expect(toast.success).toHaveBeenCalledWith('更新空间信息成功')
  })

  it('调用方说不要提示（比如它自己会拼一句更具体的）时就一句都不弹', async () => {
    await data().fetchSpace(SPACE_ID)

    await data().updateSpace(SPACE_ID, { name: 'x' }, false)

    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('失败往外抛，并让调用方自己接着', async () => {
    spaceUpdate.mockRejectedValue(new Error('500'))

    await expect(data().updateSpace(SPACE_ID, { name: 'x' })).rejects.toThrow('500')
    expect(toast.error).toHaveBeenCalledWith('更新空间信息失败')
  })
})

describe('模板与公告', () => {
  beforeEach(async () => {
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockClear()
  })

  it('模板存回去是序列化之后的那一串，提示交给调用方', async () => {
    await data().updateTemplates([{ title: '模板' }] as never[])

    expect(spaceUpdate).toHaveBeenCalledWith(SPACE_ID, {
      taskTemplates: JSON.stringify([{ title: '模板' }]),
    })
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('删模板按下标删，改的是存进去的那一份', async () => {
    spaceDetail.mockResolvedValue({
      data: { space: space(SPACE_ID, { taskTemplates: JSON.stringify([{ title: '甲' }, { title: '乙' }]) }) },
    })
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockClear()

    await data().deleteTemplate(0)

    expect(spaceUpdate).toHaveBeenCalledWith(SPACE_ID, {
      taskTemplates: JSON.stringify([{ title: '乙' }]),
    })
  })

  it('加公告是整串重写：新的接在原来的后面', async () => {
    spaceDetail.mockResolvedValue({
      data: { space: space(SPACE_ID, { announcements: JSON.stringify([{ title: '旧的' }]) }) },
    })
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockClear()

    await data().addAnnouncement({ title: '新的' } as never)

    expect(spaceUpdate).toHaveBeenCalledWith(SPACE_ID, {
      announcements: JSON.stringify([{ title: '旧的' }, { title: '新的' }]),
    })
  })

  it('改公告按下标整条替换 —— 弹窗里没有的字段会跟着一起没，这是它一直的样子', async () => {
    spaceDetail.mockResolvedValue({
      data: {
        space: space(SPACE_ID, {
          announcements: JSON.stringify([{ title: '置顶的', pinned: true, content: '<p>x</p>' }]),
        }),
      },
    })
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockClear()

    await data().updateAnnouncement(0, { title: '改过标题' } as never)

    expect(spaceUpdate).toHaveBeenCalledWith(SPACE_ID, {
      announcements: JSON.stringify([{ title: '改过标题' }]),
    })
  })

  it('公告的增删改都不自己弹提示（页面自己拼一句带名字的）', async () => {
    await data().addAnnouncement({ title: '新的' } as never)
    await data().deleteAnnouncement(0)

    expect(toast.success).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })
})

describe('管理员', () => {
  beforeEach(async () => {
    await data().fetchSpace(SPACE_ID)
  })

  it('加、改都用服务端回来的那版板', async () => {
    addAdmin.mockResolvedValue({ data: { space: space(SPACE_ID, { admins: [] }) } })
    await data().addAdmin(9, 'ADMIN')
    expect(addAdmin).toHaveBeenCalledWith(SPACE_ID, { userId: 9, role: 'ADMIN' })
    expect(state().currentSpace?.admins).toEqual([])

    updateAdmin.mockResolvedValue({ data: { space: space(SPACE_ID, { name: '改过' }) } })
    await data().updateAdmin(9, 'OWNER')
    expect(state().currentSpace?.name).toBe('改过')
  })

  it('移除管理员之后重新取一遍板 —— 被移除的那个人还在 admins 里，服务端那句话才是准的', async () => {
    spaceDetail.mockClear()

    await data().removeAdmin(9)

    expect(removeAdmin).toHaveBeenCalledWith(SPACE_ID, 9)
    expect(spaceDetail).toHaveBeenCalledWith(SPACE_ID, {
      queryClassificationTopics: true,
      queryMyRank: true,
    })
  })
})

describe('分类话题', () => {
  beforeEach(async () => {
    spaceDetail.mockResolvedValue({
      data: { space: space(SPACE_ID, { classificationTopics: [{ id: 1 }, { id: 2 }] }) },
    })
    // 服务端把改完之后的那张名单原样送回来，就像真接口那样。
    spaceUpdate.mockImplementation(async (id: number, patch: { classificationTopics: number[] }) => ({
      data: { space: space(id, { classificationTopics: patch.classificationTopics.map((tid) => ({ id: tid })) }) },
    }))
    await data().fetchSpace(SPACE_ID)
    spaceUpdate.mockClear()
  })

  it('加一个、加一批、删一个都是「改板上的那张名单」，不是各走一条接口', async () => {
    await data().addClassificationTopic(3)
    expect(spaceUpdate).toHaveBeenLastCalledWith(SPACE_ID, { classificationTopics: [1, 2, 3] })

    await data().addClassificationTopics([4, 5])
    expect(spaceUpdate).toHaveBeenLastCalledWith(SPACE_ID, { classificationTopics: [1, 2, 3, 4, 5] })

    await data().deleteClassificationTopic(1)
    expect(spaceUpdate).toHaveBeenLastCalledWith(SPACE_ID, { classificationTopics: [2, 3, 4, 5] })
  })

  it('这几条失败了不往外抛 —— 页面上那两处点完本来就是自己弹一句话', async () => {
    spaceUpdate.mockRejectedValue(new Error('403'))

    await expect(data().addClassificationTopics([4])).resolves.toBeUndefined()
    expect(toast.error).toHaveBeenCalledWith('更新分类话题失败')
  })
})
