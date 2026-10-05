// 题目板的取数：读一块板、改一块板、管它的分类、管理员和分类话题。
//
// 原先这些动作长在 `stores/space.ts` 里，store 自己 import 接口。搬出来是为了让
// 「读 store」不再等于「会发请求」：页面拿一份假状态就能单独渲染（见
// `docs/manual/dev/scenes.md`）。搬的时候一条行为都没改 —— 发同样的请求、同样的
// 时机、同样的错误处理、同样的缓存，`composables/__tests__/useSpaceData.spec.ts`
// 在搬之前就先钉住了它们。
//
// 判据留在这一层：换板要不要先清空、哪几个动作失败往外抛（调用方要接着决定弹窗关
// 不关）、哪几个自己弹一句话就算了（调用方本来就会自己弹一句更具体的）。状态全部
// 写进 store，这一层自己不持有任何东西。
import type { SpaceAdminRoleType, SpaceTaskTemplate } from '@/types'

import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { SpacesApi } from '@/network/api/spaces'
import { PatchSpaceCategoryRequestData, PatchSpaceRequestData } from '@/network/api/spaces/types'
import { TasksApi } from '@/network/api/tasks'
import { useSpaceStore } from '@/stores/space'

export function useSpaceData() {
  const space = useSpaceStore()

  const fetchSpace = async (spaceId: number) => {
    // 换了一块板：新板还没回来之前，页面上不该留着上一块板的正文和分类。
    if (spaceId !== space.currentSpaceId) {
      space.setSpace(null)
      space.setCategories([])
      space.setPendingAuditCount(0)
    }
    space.setCurrentSpaceId(spaceId)

    try {
      const { data } = await SpacesApi.detail(spaceId, { queryClassificationTopics: true, queryMyRank: true })
      space.setSpace(data.space)
    } catch (error) {
      console.error('获取题目板信息失败:', error)
      toast.error(t('spaces.detail.data.loadFailed'))
    }
  }

  /** 读一遍待审核的题目数：用审核队列同一个列表接口，只要总数。 */
  const fetchPendingAuditCount = async () => {
    const spaceId = space.currentSpaceId
    if (!spaceId) return
    try {
      const { data } = await TasksApi.list({
        space: spaceId,
        approved: 'NONE',
        pageSize: 1,
        sort_by: 'createdAt',
        sort_order: 'asc',
      })
      if (spaceId === space.currentSpaceId) space.setPendingAuditCount(data.page.total ?? 0)
    } catch (error) {
      console.error('fetch pending audit count failed', error)
    }
  }

  const updateSpace = async (spaceId: number, data: PatchSpaceRequestData, showToast = true) => {
    try {
      const response = await SpacesApi.update(spaceId, data)
      space.setSpace(response.data.space)
      if (showToast) {
        toast.success(t('spaces.detail.data.updateSuccess'))
      }
    } catch (error) {
      console.error('更新题目板信息失败:', error)
      if (showToast) {
        toast.error(t('spaces.detail.data.updateFailed'))
      }
      throw error
    }
  }

  const updateTemplates = async (newTemplates: SpaceTaskTemplate[]) => {
    if (!space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { taskTemplates: JSON.stringify(newTemplates) }, false)
    } catch (error) {
      console.error('更新模板失败:', error)
      toast.error(t('spaces.detail.data.templatesFailed'))
      throw error
    }
  }

  const deleteTemplate = async (index: number) => {
    const newTemplates = [...space.templates]
    newTemplates.splice(index, 1)
    await updateTemplates(newTemplates)
  }

  const updateClassificationTopics = async (topicIds: number[]) => {
    if (!space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { classificationTopics: topicIds }, false)
    } catch (error) {
      console.error('更新分类话题失败:', error)
      toast.error(t('spaces.detail.data.classificationTopicsFailed'))
    }
  }

  const addClassificationTopic = async (topicId: number) => {
    const updatedTopicIds = [...space.classificationTopics.map((topic) => topic.id), topicId]
    await updateClassificationTopics(updatedTopicIds)
  }

  const addClassificationTopics = async (topicIds: number[]) => {
    const updatedTopicIds = [...space.classificationTopics.map((topic) => topic.id), ...topicIds]
    await updateClassificationTopics(updatedTopicIds)
  }

  const deleteClassificationTopic = async (topicId: number) => {
    const updatedTopicIds = space.classificationTopics.map((topic) => topic.id).filter((id) => id !== topicId)
    await updateClassificationTopics(updatedTopicIds)
  }

  // Domain groups
  const fetchDomainGroups = async (spaceId?: number) => {
    const id = spaceId ?? space.currentSpaceId
    if (!id) return
    try {
      const { data } = await SpacesApi.listDomainGroups(id)
      space.setDomainGroups(data.groups ?? [])
    } catch (error) {
      console.error('获取域名组失败:', error)
    }
  }

  // Categories related methods
  // 返回读失败的那个错（读成功为 null）：调用方要能在列表区用错误态替换掉「暂无
  // 分类」——光弹一条 toast 分不出「读失败」和「本来就没有」。行为一字未改，只是把
  // 原本吞掉的错多交出去一份。
  const fetchCategories = async (includeArchived = false): Promise<unknown | null> => {
    if (!space.currentSpaceId) return null

    space.setLoadingCategories(true)
    try {
      const { data } = await SpacesApi.listCategories(space.currentSpaceId, { includeArchived })
      space.setCategories(data.categories)
      return null
    } catch (error) {
      console.error('获取分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.loadFailed'))
      return error
    } finally {
      space.setLoadingCategories(false)
    }
  }

  const createCategory = async (name: string, description?: string | null, displayOrder?: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.createCategory(space.currentSpaceId, { name, description, displayOrder })
      await fetchCategories()
      toast.success(t('spaces.detail.manageCategories.createSuccess'))
    } catch (error) {
      console.error('创建分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.createFailed'))
      throw error
    }
  }

  const updateCategory = async (categoryId: number, data: PatchSpaceCategoryRequestData) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.updateCategory(space.currentSpaceId, categoryId, data)
      await fetchCategories()
      toast.success(t('spaces.detail.manageCategories.updateSuccess'))
    } catch (error) {
      console.error('更新分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.updateFailed'))
      throw error
    }
  }

  const deleteCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.deleteCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success(t('spaces.detail.manageCategories.deleteSuccess'))
    } catch (error) {
      console.error('删除分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.deleteFailed'))
      throw error
    }
  }

  const archiveCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.archiveCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success(t('spaces.detail.manageCategories.archiveSuccess'))
    } catch (error) {
      console.error('归档分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.archiveFailed'))
      throw error
    }
  }

  const unarchiveCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.unarchiveCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success(t('spaces.detail.manageCategories.unarchiveSuccess'))
    } catch (error) {
      console.error('恢复分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.unarchiveFailed'))
      throw error
    }
  }

  const setDefaultCategory = async (categoryId: number) => {
    if (!space.currentSpaceId || !space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { defaultCategoryId: categoryId }, false)
      toast.success(t('spaces.detail.manageCategories.setDefaultSuccess'))
    } catch (error) {
      console.error('设置默认分类失败:', error)
      toast.error(t('spaces.detail.manageCategories.setDefaultFailed'))
      throw error
    }
  }

  // 管理员相关方法
  const addAdmin = async (userId: number, role: SpaceAdminRoleType) => {
    if (!space.currentSpaceId) return

    try {
      const { data } = await SpacesApi.addAdmin(space.currentSpaceId, { userId, role })
      space.setSpace(data.space)
      toast.success(t('spaces.detail.data.adminAddSuccess'))
    } catch (error) {
      console.error('添加管理员失败:', error)
      toast.error(t('spaces.detail.data.adminAddFailed'))
      throw error
    }
  }

  const updateAdmin = async (userId: number, role: SpaceAdminRoleType) => {
    if (!space.currentSpaceId) return

    try {
      const { data } = await SpacesApi.updateAdmin(space.currentSpaceId, userId, { role })
      space.setSpace(data.space)
      toast.success(t('spaces.detail.data.adminRoleSuccess'))
    } catch (error) {
      console.error('更新管理员角色失败:', error)
      toast.error(t('spaces.detail.data.adminRoleFailed'))
      throw error
    }
  }

  const removeAdmin = async (userId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.removeAdmin(space.currentSpaceId, userId)
      // 重新获取空间信息以更新管理员列表
      await fetchSpace(space.currentSpaceId)
      toast.success(t('spaces.detail.data.adminRemoveSuccess'))
    } catch (error) {
      console.error('移除管理员失败:', error)
      toast.error(t('spaces.detail.data.adminRemoveFailed'))
      throw error
    }
  }

  return {
    fetchPendingAuditCount,
    fetchSpace,
    updateSpace,
    updateTemplates,
    deleteTemplate,
    updateClassificationTopics,
    addClassificationTopic,
    addClassificationTopics,
    deleteClassificationTopic,
    fetchDomainGroups,
    fetchCategories,
    createCategory,
    updateCategory,
    deleteCategory,
    archiveCategory,
    unarchiveCategory,
    setDefaultCategory,
    addAdmin,
    updateAdmin,
    removeAdmin,
  }
}
