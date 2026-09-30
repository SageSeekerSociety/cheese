import type { DomainGroup, Space, SpaceAdminRoleType, SpaceCategory, SpaceTaskTemplate, Topic } from '@/types'

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import AccountService from '@/services/account'

/**
 * 一块题目板：它是谁、有哪些分类、有哪些域名组。
 *
 * **取数不在这里。** 发请求的那二十来个动作住在 `composables/useSpaceData.ts`，
 * 它们把拿回来的东西写进下面这几个 ref。这一层因此不认识网络 —— 整个文件的
 * import 里没有 `@/network`，也没有 `toast`：换一块板要不要先清空、失败了弹哪句话、
 * 写完之后重新拉哪一份，都是取数那一层的判断。
 *
 * 这么切的用处是「场景能单独渲染」（`docs/manual/dev/scenes.md`）：页面拿一份假状态
 * 就能画，不再因为读了这个 store 就顺带把接口拖进渲染过程。
 */
export const useSpaceStore = defineStore('space', () => {
  const currentSpace = ref<Space | null>(null)
  const currentSpaceId = ref<number | null>(null)
  const categories = ref<SpaceCategory[]>([])
  const loadingCategories = ref(false)
  const domainGroups = ref<DomainGroup[]>([])

  /** 待审核的题目数：侧栏「待审核」旁边那个数。只有管理员那一侧会去读。 */
  const pendingAuditCount = ref(0)

  /** 我在这个空间里的角色：管理员名单（含所有者）里有我就是那个角色，否则是成员。 */
  const myRole = computed<SpaceAdminRoleType | 'MEMBER'>(() => {
    const me = AccountService._user.value?.id
    const mine = currentSpace.value?.admins?.find((admin) => admin.user.id === me)
    return mine ? mine.role : 'MEMBER'
  })
  /** 能审题、看数据、管分类与邀请码：所有者和管理员。 */
  const isManager = computed(() => myRole.value !== 'MEMBER')
  /** 只有所有者能改别人的角色、转让所有者、删除空间。 */
  const isOwner = computed(() => myRole.value === 'OWNER')

  const templates = computed<SpaceTaskTemplate[]>(() => {
    if (!currentSpace.value) return []
    try {
      return JSON.parse(currentSpace.value.taskTemplates || '[]')
    } catch (error) {
      console.error('解析模板失败:', error)
      return []
    }
  })

  const classificationTopics = computed<Topic[]>(() => {
    if (!currentSpace.value) return []
    return currentSpace.value.classificationTopics || []
  })

  // ---- 写入：只有取数那一层和「把人带到这块板」的地方会用 ----

  const setSpace = (space: Space | null) => {
    currentSpace.value = space
  }

  const setCurrentSpaceId = (spaceId: number | null) => {
    currentSpaceId.value = spaceId
  }

  const setCategories = (next: SpaceCategory[]) => {
    categories.value = next
  }

  const setLoadingCategories = (loading: boolean) => {
    loadingCategories.value = loading
  }

  const setDomainGroups = (next: DomainGroup[]) => {
    domainGroups.value = next
  }

  const setPendingAuditCount = (count: number) => {
    pendingAuditCount.value = count
  }

  return {
    currentSpace,
    currentSpaceId,
    templates,
    classificationTopics,
    categories,
    loadingCategories,
    domainGroups,
    pendingAuditCount,
    myRole,
    isManager,
    isOwner,
    setSpace,
    setCurrentSpaceId,
    setCategories,
    setLoadingCategories,
    setDomainGroups,
    setPendingAuditCount,
  }
})
