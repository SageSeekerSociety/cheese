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
import type { SpaceAdminRoleType, SpaceAnnouncement, SpaceTaskTemplate } from '@/types'

import { toast } from 'vuetify-sonner'

import { SpacesApi } from '@/network/api/spaces'
import { PatchSpaceCategoryRequestData, PatchSpaceRequestData } from '@/network/api/spaces/types'
import { useSpaceStore } from '@/stores/space'

export function useSpaceData() {
  const space = useSpaceStore()

  const fetchSpace = async (spaceId: number) => {
    // 换了一块板：新板还没回来之前，页面上不该留着上一块板的正文和分类。
    if (spaceId !== space.currentSpaceId) {
      space.setSpace(null)
      space.setCategories([])
    }
    space.setCurrentSpaceId(spaceId)

    try {
      const { data } = await SpacesApi.detail(spaceId, { queryClassificationTopics: true, queryMyRank: true })
      space.setSpace(data.space)
    } catch (error) {
      console.error('获取题目板信息失败:', error)
      toast.error('获取空间信息失败')
    }
  }

  const updateSpace = async (spaceId: number, data: PatchSpaceRequestData, showToast = true) => {
    try {
      const response = await SpacesApi.update(spaceId, data)
      space.setSpace(response.data.space)
      if (showToast) {
        toast.success('更新空间信息成功')
      }
    } catch (error) {
      console.error('更新题目板信息失败:', error)
      if (showToast) {
        toast.error('更新空间信息失败')
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
      toast.error('更新模板失败')
      throw error
    }
  }

  const deleteTemplate = async (index: number) => {
    const newTemplates = [...space.templates]
    newTemplates.splice(index, 1)
    await updateTemplates(newTemplates)
  }

  const updateAnnouncements = async (newAnnouncements: SpaceAnnouncement[]) => {
    if (!space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { announcements: JSON.stringify(newAnnouncements) }, false)
    } catch (error) {
      console.error('更新公告失败:', error)
      toast.error('更新公告失败')
      throw error
    }
  }

  const addAnnouncement = async (announcement: SpaceAnnouncement) => {
    const updatedAnnouncements = [...space.announcements, announcement]
    await updateAnnouncements(updatedAnnouncements)
  }

  const updateAnnouncement = async (index: number, announcement: SpaceAnnouncement) => {
    const updatedAnnouncements = [...space.announcements]
    updatedAnnouncements[index] = announcement
    await updateAnnouncements(updatedAnnouncements)
  }

  const deleteAnnouncement = async (index: number) => {
    const updatedAnnouncements = space.announcements.filter((_, i) => i !== index)
    await updateAnnouncements(updatedAnnouncements)
  }

  const updateClassificationTopics = async (topicIds: number[]) => {
    if (!space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { classificationTopics: topicIds }, false)
    } catch (error) {
      console.error('更新分类话题失败:', error)
      toast.error('更新分类话题失败')
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
  const fetchCategories = async (includeArchived = false) => {
    if (!space.currentSpaceId) return

    space.setLoadingCategories(true)
    try {
      const { data } = await SpacesApi.listCategories(space.currentSpaceId, { includeArchived })
      space.setCategories(data.categories)
    } catch (error) {
      console.error('获取分类失败:', error)
      toast.error('获取分类失败')
    } finally {
      space.setLoadingCategories(false)
    }
  }

  const createCategory = async (name: string, description?: string | null, displayOrder?: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.createCategory(space.currentSpaceId, { name, description, displayOrder })
      await fetchCategories()
      toast.success('创建分类成功')
    } catch (error) {
      console.error('创建分类失败:', error)
      toast.error('创建分类失败')
      throw error
    }
  }

  const updateCategory = async (categoryId: number, data: PatchSpaceCategoryRequestData) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.updateCategory(space.currentSpaceId, categoryId, data)
      await fetchCategories()
      toast.success('更新分类成功')
    } catch (error) {
      console.error('更新分类失败:', error)
      toast.error('更新分类失败')
      throw error
    }
  }

  const deleteCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.deleteCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success('删除分类成功')
    } catch (error) {
      console.error('删除分类失败:', error)
      toast.error('删除分类失败')
      throw error
    }
  }

  const archiveCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.archiveCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success('归档分类成功')
    } catch (error) {
      console.error('归档分类失败:', error)
      toast.error('归档分类失败')
      throw error
    }
  }

  const unarchiveCategory = async (categoryId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.unarchiveCategory(space.currentSpaceId, categoryId)
      await fetchCategories()
      toast.success('恢复分类成功')
    } catch (error) {
      console.error('恢复分类失败:', error)
      toast.error('恢复分类失败')
      throw error
    }
  }

  const setDefaultCategory = async (categoryId: number) => {
    if (!space.currentSpaceId || !space.currentSpace) return

    try {
      await updateSpace(space.currentSpace.id, { defaultCategoryId: categoryId }, false)
      toast.success('设置默认分类成功')
    } catch (error) {
      console.error('设置默认分类失败:', error)
      toast.error('设置默认分类失败')
      throw error
    }
  }

  // 管理员相关方法
  const addAdmin = async (userId: number, role: SpaceAdminRoleType) => {
    if (!space.currentSpaceId) return

    try {
      const { data } = await SpacesApi.addAdmin(space.currentSpaceId, { userId, role })
      space.setSpace(data.space)
      toast.success('添加管理员成功')
    } catch (error) {
      console.error('添加管理员失败:', error)
      toast.error('添加管理员失败')
      throw error
    }
  }

  const updateAdmin = async (userId: number, role: SpaceAdminRoleType) => {
    if (!space.currentSpaceId) return

    try {
      const { data } = await SpacesApi.updateAdmin(space.currentSpaceId, userId, { role })
      space.setSpace(data.space)
      toast.success('更新管理员角色成功')
    } catch (error) {
      console.error('更新管理员角色失败:', error)
      toast.error('更新管理员角色失败')
      throw error
    }
  }

  const removeAdmin = async (userId: number) => {
    if (!space.currentSpaceId) return

    try {
      await SpacesApi.removeAdmin(space.currentSpaceId, userId)
      // 重新获取空间信息以更新管理员列表
      await fetchSpace(space.currentSpaceId)
      toast.success('移除管理员成功')
    } catch (error) {
      console.error('移除管理员失败:', error)
      toast.error('移除管理员失败')
      throw error
    }
  }

  return {
    fetchSpace,
    updateSpace,
    updateTemplates,
    deleteTemplate,
    updateAnnouncements,
    addAnnouncement,
    updateAnnouncement,
    deleteAnnouncement,
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
