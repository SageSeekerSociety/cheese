/**
 * 知识库页（`/teams/:handle/knowledge`）那一半「页在替谁说话」的东西。
 *
 * 拆之前这些全部长在 `views/teams/detail/Knowledge.vue` 里：取一页、筛、打开详情、
 * 打开链接、确认删除、把草稿变成一条资料。页现在只剩接线（见那个文件的顶部注释），
 * 判断都在这里 —— 也因此它们才测得动：这一份不需要挂 DOM。
 *
 * 团队从 `teamDataInjectionKey` 里取（URL 里是 handle，id 是团队页装进来的那份），
 * 所以 `inject` 必须在页的 setup 里发生：`useTeamKnowledge()` 只能从 setup 调。
 */
import type { KnowledgeDraft } from '@/lib/knowledgeDraft'
import type { CreateKnowledgeRequest, Knowledge, KnowledgeContentData, ListKnowledgesParams, Page } from '@/types'

import { computed, inject, onMounted, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { teamDataInjectionKey } from '@/keys'
import { knowledgeDraftContent, materialKindForFile } from '@/lib/knowledgeDraft'
import { resourceTypeCode } from '@/lib/knowledgeFormat'
import { KnowledgesApi } from '@/network/api/knowledges'
import { MaterialsApi } from '@/network/api/materials'
import { useDialog } from '@/plugins/dialog'
import { currentUserId } from '@/services/account'
import { parseKnowledgeContent, stringifyKnowledgeContent } from '@/types'

/** 一页取多少。列表接口是按页给的，这一页只显示第一页，见下。 */
const PAGE_SIZE = 20

export function useTeamKnowledge() {
  const dialog = useDialog()

  // URL 里是 handle，id 来自团队页装进来的那份数据。
  const teamData = inject(teamDataInjectionKey, ref())
  const teamId = computed(() => teamData.value?.id ?? 0)

  // 列表
  const loading = ref(true)
  const viewMode = ref('grid')
  const resourceDetailDialog = ref(false)
  const selectedResource = ref<Knowledge | null>(null)
  const selectedResourceContent = ref<KnowledgeContentData>({})
  const knowledges = ref<Knowledge[]>([])
  const page = ref<Page>({ pageSize: PAGE_SIZE, hasMore: false, total: 0 })

  // 筛选：三个都是一改就重取，没有本地过滤那一层。
  const searchQuery = ref<string | null>('')
  const filter = ref<{ type: string | null; tag: string | null }>({ type: null, tag: null })

  // 上传
  const uploadDialog = ref(false)
  const uploading = ref(false)

  const hasFilters = computed(() => !!(searchQuery.value || filter.value.type || filter.value.tag))

  /** 标签下拉的选项：从**已经取回来的**那几条里收，不额外打一次接口。 */
  const availableTags = computed(() => {
    const tags = new Set<string>()
    knowledges.value.forEach((resource) => {
      resource.labels?.forEach((tag: string) => tags.add(tag))
    })
    return Array.from(tags)
  })

  /** 当前登录的人。删除键按它出现，由页递到卡片 / 表格行上。 */
  const ownerId = currentUserId

  watch(teamId, () => {
    loadKnowledges()
  })

  /** 取一页。失败退成空列表，不是把上一次的结果留在页上。 */
  const loadKnowledges = async () => {
    try {
      loading.value = true

      const params: ListKnowledgesParams = {
        teamId: teamId.value,
        pageSize: PAGE_SIZE,
        sort_by: 'createdAt',
        sort_order: 'desc',
      }

      if (searchQuery.value) {
        params.query = searchQuery.value
      }

      if (filter.value.type) {
        params.type = resourceTypeCode(filter.value.type)
      }

      if (filter.value.tag) {
        params.labels = [filter.value.tag]
      }

      const { knowledges: fetchedKnowledges, page: fetchedPage } = await KnowledgesApi.list(params).then(
        (res) => res.data
      )

      knowledges.value = fetchedKnowledges
      page.value = {
        pageStart: fetchedPage.pageStart,
        pageSize: fetchedPage.pageSize,
        hasMore: fetchedPage.hasMore,
        nextStart: fetchedPage.nextStart,
        total: fetchedPage.total || 0,
      }
    } catch (error) {
      console.error('获取知识库资源失败', error)
      knowledges.value = []
    } finally {
      loading.value = false
    }
  }

  const openResourceDetail = (resource: Knowledge) => {
    selectedResource.value = resource
    selectedResourceContent.value = parseKnowledgeContent(resource)
    resourceDetailDialog.value = true
  }

  /** 打开资料：链接开内容里的 url，材料开文件，其余开记录上那个 url。 */
  const openResourceLink = (resource: Knowledge) => {
    const contentData = parseKnowledgeContent(resource)
    if (resource.type === 'LINK' && contentData.url) {
      window.open(contentData.url, '_blank')
    } else if (resource.material?.url) {
      window.open(resource.material.url, '_blank')
    } else if (resource.url) {
      window.open(resource.url, '_blank')
    }
  }

  /** 删除：先问一句，确认之后打接口并把这一条从页上摘掉（不重新取一页）。 */
  const confirmDeleteResource = async (resource: Knowledge) => {
    const result = await dialog
      .confirm(`确定要删除资源"${resource.name}"吗？此操作不可撤销。`, {
        title: '删除资源',
      })
      .wait()

    if (result) {
      try {
        await KnowledgesApi.deleteKnowledge(resource.id)

        knowledges.value = knowledges.value.filter((r) => r.id !== resource.id)

        if (resourceDetailDialog.value) {
          resourceDetailDialog.value = false
        }
      } catch (error) {
        console.error('删除资源失败', error)
        dialog.alert('删除资源失败，请稍后重试。')
      }
    }
  }

  const openUploadDialog = () => {
    uploadDialog.value = true
  }

  /**
   * 草稿 → 一条资料。材料要先自己上传一次拿到 id；其余三档只是一次 create。
   *
   * 成功返回 true（页据此关掉对话框），失败返回 false 并说一句 —— 对话框留着，
   * 人还能改一改再试。这一条是上传对话框「失败不关」那个行为的全部实现。
   */
  const createKnowledge = async (draft: KnowledgeDraft): Promise<boolean> => {
    // 表单校验兜住的是格式；这三条兜的是「这一档缺了它就不能成立」。
    if (draft.type === 'MATERIAL' && !draft.file) {
      toast.error('请选择要上传的文件')
      return false
    } else if (draft.type === 'LINK' && !draft.url) {
      toast.error('请输入有效的链接地址')
      return false
    } else if (draft.type === 'CODE' && !draft.code) {
      toast.error('请输入代码内容')
      return false
    }

    uploading.value = true

    try {
      let materialId: number | undefined

      if (draft.type === 'MATERIAL' && draft.file) {
        const res = await MaterialsApi.upload(draft.file, materialKindForFile(draft.file))
        materialId = res.data.id
      }

      const request: CreateKnowledgeRequest = {
        name: draft.name,
        description: draft.description,
        type: draft.type,
        content: stringifyKnowledgeContent(knowledgeDraftContent(draft)),
        teamId: teamId.value,
        labels: draft.labels,
      }

      if (materialId) {
        request.materialId = materialId
      }

      const response = await KnowledgesApi.create(request)

      // 新的一条在最前面：这一页是倒序的，和 sort_order 一致。
      knowledges.value = [response.data.knowledge, ...knowledges.value]

      toast.success('资料上传成功')
      return true
    } catch (error) {
      console.error('上传资料失败', error)
      toast.error('上传资料失败，请稍后重试')
      return false
    } finally {
      uploading.value = false
    }
  }

  onMounted(async () => {
    await loadKnowledges()
  })

  return {
    // 列表
    loading,
    viewMode,
    searchQuery,
    filter,
    knowledges,
    hasFilters,
    availableTags,
    ownerId,
    loadKnowledges,
    // 详情
    resourceDetailDialog,
    selectedResource,
    selectedResourceContent,
    openResourceDetail,
    openResourceLink,
    confirmDeleteResource,
    // 上传
    uploadDialog,
    uploading,
    openUploadDialog,
    createKnowledge,
  }
}
