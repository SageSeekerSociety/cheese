<script setup lang="ts">
// 发题页 —— **这一页自己画的那一版**（形状权威：`proto-board/pages/Publish.vue`）。
//
// 第五批把老树的 `views/spaces/detail/PublishTask.vue` 套进新外壳，第六批在壳头上补了
// 原型那颗「手写一道 / 从 PDF 生成」；这一批把正文也接过来：**底下不再挂老页面**，
// 页头、两条路、每一张卡、以及发题这份装配，全在下面。
//
// ## 老树一个字没改
//
// `views/spaces/detail/PublishTask.vue` 与 `proto-board/` 一个字没动，老地址
// `/spaces/:id/publish` 照常在原处服务（新老并存是这个系列的设计）。这一页用的是
// **同一批真接口**：`POST /tasks` 与 `POST /tasks/publish/from-pdf/preview|confirm`，
// 语义一模一样，一个假数据都没造。
//
// ## 重用的是机制，不是布局 —— 但机制里那两件自带布局
//
// 与第八批的题目详情（`pages/TaskDetail.vue`）同一个做法：这一页自己画，底下那套成熟
// 的机器一件不重写。这里的「机器」是两件**共享组件**（老树那一页与改题页也在用）：
//
// - `components/tasks/TaskForm.vue` —— 发题表单本身：字段、校验（vee-validate + zod）、
//   发布参数，以及右栏「提交前」那张清单读的规则（表单自己按 `PUBLISH_CHECKS_SINK`
//   报上来）。它自带卡片排版，而那一整份字段是与后端一一对得上的（参与者类型、难度、
//   报名开始/截止、默认完成期限、分类、话题、实名、权限/域名组、富文本题干、视频），
//   在这一页里重排一次就等于把「表单会拦什么」这件事抄成两份 —— 所以它**原样挂**，
//   这一批不动它的排版。
// - `components/tasks/TaskAttachmentPicker.vue` —— 「附件（可选）」那张卡：选中即上传
//   （`POST /attachments`），发题请求带的是它回报的那串 id。
//
// 页面这一层全按原型画：页头与那颗切换、两块 PDF 卡片、整页两栏网格、右栏那几张卡。
// 分界就落在 `.pub__grid` 那一行上：左栏是「页面自己的卡片 + 那两件共享机器」。
//
// ## 「装完再挂」现在由这一页自己保证
//
// 表单要的每一样都从 pinia 的 `space` store 来（`categories`、`classificationTopics`、
// `domainGroups`、`templates`），而那几样取自 `GET /spaces/{id}` 与 `/categories`、
// `/domain-groups`。老树里装这份的是空间壳 `views/spaces/Detail.vue`；新外壳的题目板有
// 自己的 store（`board/store.ts`），pinia 那份没人管 —— 所以这一页自己装，而且
// **装完才挂表单**：`fetchCategories()` 在 `currentSpaceId` 为空时**直接返回**（不报错），
// 装晚了分类下拉是空的、模板也取不到，界面照样打得开，只是看着像「这块板什么都没有」。
//
// ## 两条路，同一批接口
//
// - 「手写一道」：页面自己的三张卡（PDF 快速发布 / 解析预览结果 / 附件（可选））＋
//   共享的 `TaskForm`。那条「PDF 快速发布」是老页本来就有的能力 —— 先解析出草稿，
//   再在**同一张表单**里填发布参数、一次批量发出去（走 `confirm`）—— 它在这一页里
//   与下面那条路并存，形状照原型画（原型里没有这张卡）。
// - 「从 PDF 生成」：原型那一版：上传框下面一行一行读出来草稿，**逐条能改标题与题干、
//   能勾掉不要的**，确认之后**不跳走**、就地给回执，回执里给两个去处。
//
// 真接口回什么、就摆什么：预览回来的有 `drafts`（name/intro/description/space/
// categoryId）、`templateUsed`、`tokenUsed`，以及 `attachments` —— 服务端在预览这一步
// 就把**原 PDF 与抽出的插图**落成了文件行（挂在调用者名下），把 id 报回来。结果区摆的
// 三件事分别从前三样来 —— 插图数是**从草稿正文里的图片链接数出来的**（后端把抽出的插图
// 传上存储、再把正文里的图片标记换成链接，所以图片在正文里），页码是**草稿在这份预览里
// 的次序**（后端一页一页读、按页序返回，见 `draftPage` 的注释）。这里一件都不编。
//
// 「附带给领取者」那两颗勾（原 PDF / 抽出的插图）**只画接口真给了东西的那一颗** ——
// 哪一样服务端没落成文件行，就不画那一颗、并说明为什么（见 `pdfAttachments` 与
// `attachmentIds` 的注释）。
//
// ## 少画，不编（`#1925` 的口径）
//
// - **标签**：原型那张「题目内容」卡里有一格自由标签。真库没有这一层，题目挂的是
//   `topics` —— 表单里就是那枚「话题」下拉（分类标签那张卡的第二枚 combobox），
//   所以这一页不画一排凭空贴出来的标签。
// - **附件上限**：原型那张卡脚上写着「最多 10 个，单个 20MB 以内」——两个数都是假的。
//   数量今天没有上限（想传多少份都收，服务只拦「同一个文件挂到第二道题上」），所以这
//   一句不写；大小有一个，而且是由**接口报出来的那个数**：`GET /attachments/limits`
//   报的与 `AttachmentService.upload` 拦下超限文件读的是同一个配置
//   （`settings.attachment_max_bytes`）。附件卡写的正是它（`TaskAttachmentPicker`
//   自己问、自己画），这一页不转述也不写死。文件名与大小仍旧是上传成功之后由那张卡
//   自己记着的那一份。
// - **领取与小队**：原型把它们并成一张卡（领取人数上限 / 小队规模 / 截止时间）。真库里
//   对应的是三样不同的东西（`participantLimit`、`submitterType` + 小队上下限 + 队伍
//   锁定策略、报名截止 `deadline` 与领取后默认天数 `defaultDeadline`），分在表单
//   「基本信息」与「时间设置」两张卡里 —— 这一页不把它们重排成原型那张卡，重排一次
//   就等于把校验也重写一遍（见上面对 `TaskForm` 的说明）。
// - **右栏那张「提交前」**：上面每一条都是真表单**真会拦**的规则，由表单自己报上来，
//   这一页不另发明（规则表与出处写在 `lib/taskPublishChecks.ts`）。
import type { PublishCheck } from '@/lib/taskPublishChecks'
import type { PdfPublishAttachmentsData, PdfTaskDraftData } from '@/network/api/tasks/types'
import type { TaskFormSubmitData, TaskSubmissionSchemaEntry } from '@/types'

import { computed, defineAsyncComponent, provide, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import PanelCard from '../components/PanelCard.vue'
import { BOARD_PUBLISH_DONE_ROUTE } from '../routeNames'
import { isManager } from '../store'

import { PUBLISH_CHECKS_SINK } from '@/lib/taskPublishChecks'
import { TasksApi } from '@/network/api/tasks'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

const TaskForm = defineAsyncComponent(() => import('@/components/tasks/TaskForm.vue'))
const TaskAttachmentPicker = defineAsyncComponent(() => import('@/components/tasks/TaskAttachmentPicker.vue'))

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const { currentSpaceId, templates, classificationTopics, categories, domainGroups } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))

/** 两条发题路：手写一道，或从一份 PDF 里批量生成。 */
const mode = ref<'write' | 'pdf'>('write')

// --- 装这块板（表单要的东西全在这里，见文件头「装完再挂」）----------------------

/** 装好之前不挂表单。 */
const ready = ref(false)

/** 把这一页要的那几样装上：空间自己、这块板的分类与域名组，以及地址栏点名的模板。
 *
 *  顺序有意义（见文件头「装完再挂」）：`fetchCategories()` 与 `fetchDomainGroups()`
 *  都挂在 `currentSpaceId` 上，空间没装好它们就是空操作 —— 不报错，但表单那一栏是空的。 */
async function loadSpace(id: number) {
  if (!Number.isFinite(id) || id <= 0) return
  ready.value = false
  await spaceStore.fetchSpace(id)
  // 空间换了（或者用户直接输地址进来）：下面这几样都挂在 `currentSpaceId` 上，顺序有意义。
  if (currentSpaceId.value !== id) return
  const ok = await errorHandler.withErrorHandling(
    async () => {
      await spaceStore.fetchCategories()
      await spaceStore.fetchDomainGroups()
      await loadTemplate()
      // 回调的返回值就是「装完没有」：`withErrorHandling` 栽了给的是 `undefined`，
      // 所以这里必须回一个真东西，不能靠 `undefined` 判成败（回调什么都不返回时
      // 成功与失败是同一个值）。
      return true
    },
    { defaultMessage: t('spaces.detail.publishTask.initializationFailed') }
  )
  // 装配期间空间被换掉/页面被切走时不要再放行 —— 放行了表单拿到的就是别人的分类。
  if (ok === true && currentSpaceId.value === id) ready.value = true
}

watch(spaceId, (id) => void loadSpace(id), { immediate: true })

/** 活跃（未归档）的分类，按 `displayOrder` 排 —— 与老页同一口径：下拉里只有能选的。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

/** `?categoryId=` 预选：不在活跃分类里就当没有（否则表单会拿一个选不出来的 id 去提交）。 */
const preselectedCategoryId = computed(() => {
  const param = route.query.categoryId
  if (!param) return undefined
  const id = Number(param)
  return activeCategories.value.some((cat) => cat.id === id) ? id : undefined
})

/** `?templateId=` 既喂表单（模板初值），也喂 PDF 解析（用哪份模板读 PDF），两条路同一口径。 */
const templateIdParam = computed(() => {
  const param = route.query.templateId
  if (!param || param === 'blank') return null
  const id = Number(param)
  return Number.isFinite(id) ? id : null
})

/** 模板索引。PDF 那条路要的是 `-1`（空白），见 `parsePdf`。 */
const pdfTemplateIndex = computed(() => templateIdParam.value ?? -1)

/** 表单初值：没模板就是空对象（表单自己那套默认值顶上）。 */
const initialTaskData = ref<Record<string, unknown>>({})

function loadTemplate() {
  const id = templateIdParam.value
  if (id === null) return
  const template = templates.value[id]
  if (!template) return
  initialTaskData.value = {
    name: template.title,
    description: JSON.parse(template.content),
    submitterType: template.submitterType !== null ? template.submitterType : undefined,
    rank: template.rank !== null ? template.rank : undefined,
    minTeamSize: template.minTeamSize,
    maxTeamSize: template.maxTeamSize,
    defaultDeadline: template.defaultDeadline !== null ? template.defaultDeadline : undefined,
    requireRealName: template.requireRealName !== null ? template.requireRealName : undefined,
  }
}

// --- 右栏两张卡 ---------------------------------------------------------------

/**
 * 「提交前」那张清单：底下那张真表单每变一次就报一次它现在**拦着你的**规则，
 * 报空数组就是提交得出去。规则表与这句话的出处写在 `lib/taskPublishChecks.ts`。
 *
 * `null` = 表单还没挂上（空间还在装、或者这会儿在 PDF 那条路上）—— 那时这张卡
 * 说不出「没问题」，所以按钮是灰的、也不画那一行。**不拿空数组顶上**：那是
 * 「校验过了」，跟「还没得可校验」不是一句话。
 */
const formChecks = shallowRef<PublishCheck[] | null>(null)
/** 表单交上来的它自己的提交。清单那颗「提交审核」按钮走的就是它。 */
const formSubmit = shallowRef<(() => void) | null>(null)

provide(PUBLISH_CHECKS_SINK, {
  report: (checks) => {
    formChecks.value = checks
  },
  handOverSubmit: (submit) => {
    formSubmit.value = submit
  },
})

/** 清单那颗按钮：真表单自己会拦的就让它拦（`handleSubmit` 校验不过什么都不发生）。 */
function submitFromChecklist() {
  formSubmit.value?.()
}

// --- 手写一道：附件与提交 ------------------------------------------------------

/** 随题一起发出的材料：附件卡片上传完拿到 id，发题那条请求带着它一起走。 */
const attachmentIds = ref<number[]>([])
const attachmentUploading = ref(false)

/** 这一批题用哪张提交表。后端建题那条路读它，不写题目提交页就一个输入项都没有。 */
const taskSubmissionSchema: TaskSubmissionSchemaEntry[] = [{ prompt: '提交文件', type: 'FILE' }]

/**
 * 提交。两条路走的是两个接口，但**入口只有一个**：表单的那颗提交按钮。
 *
 * - 解析过 PDF（`quickDrafts` 非空）→ 批量发布那条路（`confirm`）；
 * - 否则 → `POST /tasks` 建一道题。
 *
 * 发完落到新外壳自己的「我的」（老树那一步是「我发布的」页）：路由名从 `../routeNames`
 * 来，页面里不出现路径。
 */
async function submitTask(taskData: TaskFormSubmitData) {
  const id = currentSpaceId.value
  if (!id) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return false
  }

  if (quickDrafts.value.length > 0) return (await confirmQuickFromPdf(taskData, id)) ?? false

  // 材料还在上传就先别发：附件 id 是发题那条请求的一部分，这时候发出去就会静默地
  // 少一份用户明明已经选好的文件。
  if (attachmentUploading.value) {
    toast.error('附件还在上传，请稍候再发布')
    return false
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      const {
        data: {
          task: { approved },
        },
      } = await TasksApi.create({
        ...taskData,
        submissionSchema: taskSubmissionSchema,
        space: id,
        requireRealName: taskData.requireRealName || false,
        categoryId: taskData.categoryId,
        accessControlEnabled: taskData.accessControlEnabled || false,
        accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
        attachmentIds: attachmentIds.value.length > 0 ? attachmentIds.value : undefined,
      })

      if (!approved) {
        toast.success(t('spaces.detail.publishTask.createSuccessAndWaitingAudit'))
      } else {
        toast.success(t('spaces.detail.publishTask.createSuccess'))
      }

      router.replace({ name: BOARD_PUBLISH_DONE_ROUTE, params: { spaceId: id } })
      return approved
    },
    {
      defaultMessage: t('spaces.detail.publishTask.createFailed'),
    }
  )

  return result !== undefined
}

// --- 手写一道：PDF 快速发布（老页本来那张卡）-----------------------------------

/** 真接口那三条上限：后端 `preview_task_from_pdf` 里写死的 15MB 与 1..20。
 *  与「从 PDF 生成」那条路同源（同一张卡在两条路上都画）。 */
const MAX_PDF_BYTES = 15 * 1024 * 1024
const MAX_DRAFTS = 20

const quickFile = ref<File | File[] | null>(null)
const quickLoading = ref(false)
const quickConfirming = ref(false)

/** 解析出来的草稿（`PdfTaskDraftData` 原样），以及这次烧掉多少 token。 */
const quickDrafts = ref<PdfTaskDraftData[]>([])
const quickTokens = ref<number | null>(null)

/** `v-file-input` 单文件/多文件两种返回形状都出现过，统一成一份。 */
const selectedQuickPdf = computed<File | null>(() => {
  if (Array.isArray(quickFile.value)) return quickFile.value[0] ?? null
  return quickFile.value
})

/** 草稿内容预览。原型那一版能就地改题干，这一条**不能** —— 它落到的还是下面那张
 *  共享表单（发布参数在表单里填），能改的是「从 PDF 生成」那条路。 */
function previewDescription(value: unknown): string {
  if (!value) return '—'
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text.length > 180 ? `${text.slice(0, 180)}…` : text || '—'
}

/** 解析预览：真的把这份 PDF 发给后端（`POST /tasks/publish/from-pdf/preview`）。 */
async function previewFromPdf() {
  const id = currentSpaceId.value
  const file = selectedQuickPdf.value
  if (!id) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return
  }
  if (!file) {
    toast.error('请先选择要上传的 PDF 文件')
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    toast.error('PDF 文件不能超过 15MB')
    return
  }

  quickLoading.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId: id,
      file,
      categoryId: preselectedCategoryId.value,
      templateIndex: pdfTemplateIndex.value,
      maxTasks: MAX_DRAFTS,
    })

    if (!data.drafts || data.drafts.length === 0) {
      toast.error('未识别到可发布的题目草稿')
      quickDrafts.value = []
      quickTokens.value = data.tokenUsed ?? null
      initialTaskData.value = {}
      return
    }

    quickDrafts.value = data.drafts
    initialTaskData.value = { name: data.drafts[0]?.name || 'PDF 批量发布参数' }
    quickTokens.value = data.tokenUsed ?? null
    toast.success(`解析完成，共识别 ${data.drafts.length} 个题目草稿`)
  } catch (error) {
    console.error('PDF 解析预览失败:', error)
    toast.error('PDF 解析预览失败')
  } finally {
    quickLoading.value = false
  }
}

/** 清空预览：表单也从「只填参数」回到「手写一道」那张完整的表单。 */
function clearQuickDrafts() {
  quickDrafts.value = []
  quickTokens.value = null
  initialTaskData.value = {}
}

/** 批量发布那条路共用的一半参数（后端 `_apply_pdf_task_options` 把它与每条草稿合起来）。 */
function buildTaskOptions(taskData: TaskFormSubmitData, id: number) {
  return {
    ...taskData,
    submissionSchema: taskSubmissionSchema,
    space: id,
    name: taskData.name || 'PDF 批量发布参数',
    intro: '',
    description: '',
    requireRealName: taskData.requireRealName || false,
    categoryId: taskData.categoryId,
    accessControlEnabled: taskData.accessControlEnabled || false,
    accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
  }
}

/** 确认并批量发布草稿（`POST /tasks/publish/from-pdf/confirm`），发完落到「我的」。 */
async function confirmQuickFromPdf(taskData: TaskFormSubmitData, id: number) {
  if (quickDrafts.value.length === 0) {
    toast.error('没有可发布的草稿，请先解析预览')
    return false
  }

  quickConfirming.value = true
  try {
    const { data } = await TasksApi.confirmFromPdf({
      drafts: quickDrafts.value,
      taskOptions: buildTaskOptions(taskData, id),
    })
    toast.success(`已发布 ${data.count || data.tasks.length} 个题目`)
    quickDrafts.value = []
    quickTokens.value = null
    initialTaskData.value = {}
    router.replace({ name: BOARD_PUBLISH_DONE_ROUTE, params: { spaceId: id } })
    return true
  } catch (error) {
    console.error('PDF 批量发布失败:', error)
    toast.error('PDF 批量发布失败')
    return false
  } finally {
    quickConfirming.value = false
  }
}

// --- 从 PDF 生成 --------------------------------------------------------------

const fileInput = ref<File | File[] | null>(null)
const parsing = ref(false)
const confirming = ref(false)
const pdfError = ref('')

/** `v-file-input` 单文件/多文件两种返回形状都出现过，统一成一份。 */
const selectedPdf = computed<File | null>(() => {
  if (Array.isArray(fileInput.value)) return fileInput.value[0] ?? null
  return fileInput.value
})

/** 一条待确认的草稿。字段就是真接口给的那几个，外加两样界面要用的：勾选与出处。 */
interface PdfDraft {
  key: string
  picked: boolean
  name: string
  intro: string
  description: string
  categoryId?: number
  /** 出处页。见 `draftPage`。 */
  page: number
  /** 这份草稿正文里带几张图（后端抽出来嵌进正文的那些）。 */
  images: number
}

const drafts = ref<PdfDraft[]>([])
/** 预览回来的另外两样：用了哪份模板、烧了多少 token。 */
const parsedMeta = ref<{ template: string; images: number; tokens: number } | null>(null)
/** 确认发布之后的回执。**不跳走**：就地告诉人刚发了什么、下一步去哪儿看。 */
const receipt = ref<{ count: number; tasks: { id: number; name: string }[] } | null>(null)

/**
 * 预览时服务端**已经落成文件行**、因而可以勾来当附件的那些东西。
 *
 * 这是预览那一步的副作用（`POST /tasks/publish/from-pdf/preview` 把原 PDF 上传、
 * 把抽出的插图登记成 `Attachment`，都挂在调用者名下），id 从响应里的 `attachments`
 * 报回来。**老后端不回这一项**时这里是 `null` —— 那就一颗勾都不画，见下面那个
 * `v-if`，不画一颗点了没用的假勾。
 */
const pdfAttachments = ref<PdfPublishAttachmentsData | null>(null)
/** 两颗勾：默认都勾着（原型也是），但**只有接口真给了东西的那一颗才画**。 */
const attachPdf = ref(true)
const attachImages = ref(true)

const pdfImages = computed(() => pdfAttachments.value?.images ?? [])

/**
 * 勾中的附件 id —— 原 PDF 在前、插图在后，**整份清单会挂到这批题的每一道上**
 * （后端的 `attach_uploaded_to_tasks`）。一样都没勾就是空数组，这时确认请求里
 * **不带 `attachmentIds` 这一项**，行为跟这条路今天完全一样。
 */
const attachmentIdsForPdf = computed<number[]>(() => {
  const a = pdfAttachments.value
  if (!a) return []
  const ids: number[] = []
  if (attachPdf.value && a.pdf) ids.push(a.pdf.id)
  if (attachImages.value) ids.push(...a.images.map((img) => img.id))
  return ids
})

const pickedDrafts = computed(() => drafts.value.filter((d) => d.picked))

const MARKDOWN_IMAGE = /!\[[^\]]*\]\([^)]*\)/g

/** 正文里那张图片标记有几个 —— 后端把抽出的插图换成链接后就长这样。 */
function countImages(markdown: string): number {
  return markdown.match(MARKDOWN_IMAGE)?.length ?? 0
}

/**
 * 草稿的出处页。
 *
 * **真接口没有回传页码**（`PdfTaskDraftData` 只有 name/intro/description/space/
 * categoryId），后端也不说某条草稿来自哪一页。它是**一页一页读的、按页序把草稿
 * 攒起来**（`TaskPdfDraftService.generate_task_payloads_from_pdf` 顺着
 * `page_data` 走），所以「这份预览里的第 N 条」就是后端读的第 N 页 —— 某页解析
 * 失败被跳过时这个号会偏小，那是这条推导唯一会失准的地方，写在题目简介里的标记
 * 也是同一个号。
 */
function draftPage(index: number): number {
  return index + 1
}

/** `templateUsed` 是后端 `pick_template` 的原样返回：没配模板、或索引越界时是
 *  一个 `{}`，所以这里要能落回一句人话。模板元素本身有 `title`/`name` 两种写法
 *  （老页读的是 `title`，`_extract_template_defaults` 还认 `task` 那一层）。 */
function templateLabel(used: unknown): string {
  const t = used as { title?: unknown; name?: unknown; task?: { title?: unknown; name?: unknown } } | null
  const title = t?.title ?? t?.name ?? t?.task?.title ?? t?.task?.name
  return typeof title === 'string' && title.trim() ? title.trim() : '空白模板（这块板没有配题模板）'
}

/** 分类名。草稿带回的是 `categoryId`，名字要自己从这块板的分类里换。 */
function categoryLabel(id?: number): string {
  if (id === undefined) return '未指定分类（落这块板的默认分类）'
  return spaceStore.categories.find((c) => c.id === id)?.name ?? `分类 #${id}`
}

/** 失败时给人看的话，尽量用后端自己的措辞（业务错误都在 `response.data.message`）。 */
function failureText(error: unknown): string {
  const e = error as { response?: { data?: { message?: string } }; message?: string }
  return e?.response?.data?.message || e?.message || '未知错误'
}

function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
}

function resetPdf() {
  fileInput.value = null
  drafts.value = []
  parsedMeta.value = null
  pdfAttachments.value = null
  attachPdf.value = true
  attachImages.value = true
  receipt.value = null
  pdfError.value = ''
}

async function parsePdf() {
  const id = spaceStore.currentSpaceId
  const file = selectedPdf.value
  pdfError.value = ''
  receipt.value = null

  if (!id) {
    pdfError.value = '空间还没装好，稍等一下再试。'
    return
  }
  if (!file) {
    pdfError.value = '先选一份 PDF。'
    return
  }
  if (!isPdf(file)) {
    pdfError.value = '只收 PDF：这个文件既不是 application/pdf，也不叫 .pdf。'
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    pdfError.value = `单个 PDF 不能超过 15MB，这一份 ${Math.round(file.size / 1024 / 1024)}MB。`
    return
  }

  parsing.value = true
  drafts.value = []
  parsedMeta.value = null
  pdfAttachments.value = null
  attachPdf.value = true
  attachImages.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId: id,
      file,
      templateIndex: pdfTemplateIndex.value,
      maxTasks: MAX_DRAFTS,
    })

    if (!data.drafts?.length) {
      pdfError.value = `这次没解析出草稿：后端读了 PDF，但一条题也没识别出来（已消耗 ${data.tokenUsed ?? 0} tokens）。`
      return
    }

    drafts.value = data.drafts.map((d, index) => ({
      key: `${index}-${d.name || 'draft'}`,
      picked: true,
      name: d.name,
      intro: d.intro,
      description: d.description,
      categoryId: d.categoryId,
      page: draftPage(index),
      images: countImages(d.description),
    }))
    parsedMeta.value = {
      template: templateLabel(data.templateUsed),
      images: drafts.value.reduce((n, d) => n + d.images, 0),
      tokens: data.tokenUsed ?? 0,
    }
    // 服务端顺带落好的附件行。老后端没有这一项时留 null —— 那时页面上不画勾，
    // 并且说明是哪一样带不了。
    pdfAttachments.value = data.attachments ?? null
  } catch (error) {
    pdfError.value = `解析失败：${failureText(error)}`
  } finally {
    parsing.value = false
  }
}

/** 出处标记。题目模型里没有「来源」这一列，也不给它加 —— 标记写进**简介**：
 *  简介会跟着这道题一路走，审核队列那一行显示的就是它。 */
function originTag(page: number): string {
  return `【PDF · 第 ${page} 页】`
}

function toDraftPayload(draft: PdfDraft): PdfTaskDraftData {
  const payload: PdfTaskDraftData = {
    name: draft.name.trim(),
    intro: `${originTag(draft.page)}${draft.intro.trim()}`,
    description: draft.description.trim(),
    space: spaceId.value,
  }
  if (draft.categoryId !== undefined) payload.categoryId = draft.categoryId
  return payload
}

async function confirmPdf() {
  const picked = pickedDrafts.value
  const id = spaceStore.currentSpaceId
  if (!picked.length || !id) return

  confirming.value = true
  pdfError.value = ''
  // 勾中的那些文件。一样都没勾就是空数组 —— 那时**这一项不发给后端**，跑的还是这条
  // 路今天那份参数（后端读到没有 `attachmentIds` 就一道题都不挂材料）。
  const ids = attachmentIdsForPdf.value
  try {
    const { data } = await TasksApi.confirmFromPdf({
      drafts: picked.map(toDraftPayload),
      taskOptions: {
        // 这是**每道题共用**的那一半参数：后端把它和每条草稿合起来
        // （`_apply_pdf_task_options`），name/intro/description 三件逐条被草稿里那份
        // 覆盖（出处标记就在各自那份 intro 里），其余字段原样落下去。
        // 老页那条路（`buildTaskOptions`）报的也是这一份形状，这里跟它同一口径。
        name: picked[0].name.trim(),
        description: picked[0].description,
        submitterType: 'USER',
        resubmittable: true,
        editable: true,
        defaultDeadline: 30,
        deadline: null,
        space: id,
        // 提交表单这一项**是真会落库的**，不是占位：`_create_task_entity` 的两种
        // 入参形状（Pydantic 与这里的 dict）都在读它，最后一起走 `replace_schema`
        // 写进 `task_submission_schema`（`routes/tasks.py` 那行注释：「发布页总会
        // 带上这张表（至少一个『提交文件』项）。建题时不写，题目的提交页就一个
        // 输入项都没有」）。详情接口把它读回来给 `Submit.vue` 出表单。老页那条路
        // （`buildTaskOptions`）报的也是同一份。
        submissionSchema: taskSubmissionSchema,
        // 上面那两颗勾勾中的文件：**每一道**都挂同一份（后端
        // `attach_uploaded_to_tasks`）。没勾就整项不出现，这条路的形状与从前一致。
        ...(ids.length ? { attachmentIds: ids } : {}),
      },
    })

    receipt.value = {
      count: data.count || data.tasks.length,
      tasks: data.tasks.map((t) => ({ id: t.id, name: t.name })),
    }
    drafts.value = []
    parsedMeta.value = null
  } catch (error) {
    pdfError.value = `发布失败：${failureText(error)}`
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <div class="pub">
    <header class="pub__head">
      <div>
        <h1>{{ mode === 'write' ? '出一道题' : '从 PDF 生成题目' }}</h1>
        <!-- 发出去之后会经过哪几站，一句话是给谁看的，跟着身份变（与右栏那张卡同一口径）。 -->
        <p v-if="isManager" class="pub__note">
          你是所有者/管理员，发出来的题同样先进审核队列 —— 但<b>你自己就能审</b>，不必等别人。
        </p>
        <p v-else class="pub__note">发出来的题会先进审核队列，由所有者或管理员看过之后上板。</p>
      </div>
      <!-- 原型那一颗切换：手写一道，或让一份 PDF 先解析成草稿。 -->
      <v-btn-toggle v-model="mode" density="compact" variant="outlined" divided mandatory class="pub__mode">
        <v-btn value="write" size="small" prepend-icon="mdi-pencil-outline">手写一道</v-btn>
        <v-btn value="pdf" size="small" prepend-icon="mdi-file-pdf-box">从 PDF 生成</v-btn>
      </v-btn-toggle>
    </header>

    <!-- ============ 手写一道 ============ -->
    <div v-if="mode === 'write'" class="pub__grid">
      <div class="pub__main">
        <!-- 老页本来那张「PDF 快速发布」：先解析出草稿，再在下面那张表单里填参数、
             一次批量发出去。原型里没有这张卡（原型把 PDF 整条路放在另一个态里），
             所以形状照原型画：一张面板、标题加一句说明、按钮排在右下。 -->
        <PanelCard title="PDF 快速发布" subtitle="上传题目 PDF 后系统会解析出草稿；发布参数在下面那张表单里统一填写">
          <v-file-input
            v-model="quickFile"
            accept=".pdf,application/pdf"
            label="上传题目 PDF"
            variant="outlined"
            density="comfortable"
            clearable
            prepend-icon=""
            hide-details="auto"
            :disabled="quickLoading || quickConfirming"
          >
            <template #prepend>
              <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
            </template>
          </v-file-input>

          <p class="pdf__hint">
            支持 PDF 文件，单文件大小不超过 15MB。地址栏带模板参数时优先用那份模板，否则用空白模板。
          </p>

          <div class="pdf__actions">
            <span class="pdf__actions-note">解析是只读的：读一遍 PDF、生成草稿，不会直接发出去。</span>
            <v-spacer />
            <v-btn
              color="primary"
              variant="flat"
              :loading="quickLoading"
              :disabled="quickLoading || quickConfirming || !selectedQuickPdf"
              @click="previewFromPdf"
            >
              <v-icon start>mdi-eye-outline</v-icon>
              解析预览
            </v-btn>
          </div>
        </PanelCard>

        <!-- 解析预览结果：逐条摆出后端认出来的草稿。**只给看不给改** —— 参数在下面
             那张表单里填、`name` 由第一条草稿带过来；逐条能改的是「从 PDF 生成」那条路。 -->
        <PanelCard
          v-if="quickDrafts.length"
          data-testid="quick-drafts"
          title="解析预览结果"
          :subtitle="`共识别 ${quickDrafts.length} 个题目草稿，提交下面那张表单时会批量应用发布参数${
            quickTokens !== null ? `。本次约消耗 ${quickTokens} tokens` : ''
          }`"
        >
          <ul class="quick__list">
            <li v-for="(draft, index) in quickDrafts" :key="`${index}-${draft.name || 'draft'}`" class="quick__row">
              <div class="quick__row-head">
                <span class="quick__name">{{ index + 1 }}. {{ draft.name || '未命名题目' }}</span>
                <v-chip size="x-small" label variant="tonal" color="info">PDF 草稿</v-chip>
              </div>
              <p class="quick__intro">简介：{{ draft.intro || '—' }}</p>
              <p class="quick__desc">内容预览：{{ previewDescription(draft.description) }}</p>
            </li>
          </ul>

          <div class="pdf__actions">
            <span class="pdf__actions-note">
              确认之后这 {{ quickDrafts.length }} 道会一起进<b>待审核</b>队列；参数在下面那张表单里填。
            </span>
            <v-spacer />
            <v-btn variant="text" :disabled="quickConfirming" @click="clearQuickDrafts">清空预览</v-btn>
          </div>
        </PanelCard>

        <!-- 材料：选中即上传，发题那条请求带的是它的 id。 -->
        <TaskAttachmentPicker
          @update:attachment-ids="attachmentIds = $event"
          @update:uploading="attachmentUploading = $event"
        />

        <!-- 共享的发题表单（见文件头）。空间**装完再挂**：`v-if="ready"` 就是那件事。 -->
        <TaskForm
          v-if="ready"
          :initial-data="initialTaskData"
          :submit-button-text="t('tasks.publish.submit')"
          :classification-topics="classificationTopics"
          :categories="activeCategories"
          :selected-category-id="preselectedCategoryId"
          :domain-groups="domainGroups"
          :parameters-only="quickDrafts.length > 0"
          @submit="submitTask"
        />
      </div>

      <aside class="pub__side">
        <!-- 这道题发出去之后会经过哪几站。一句话是给谁看的，跟着身份变。 -->
        <PanelCard title="发出去之后">
          <ol class="pub__steps" data-testid="publish-lifecycle">
            <li><b>待审核</b> —— 题目只有你自己和管理员看得到。</li>
            <li data-testid="publish-audience">
              <b>有人审了</b> ——
              <template v-if="isManager">你可以直接通过（自己发的题自己审）。</template>
              <template v-else>所有者或管理员通过后就上板。</template>
            </li>
            <li><b>上板</b> —— 所有人可见可领，领取进度开始计。</li>
            <li><b>你能看到</b> —— 「我的 → 我发布的」里有这道题的领取走势、领取者名单和完成情况。</li>
          </ol>
          <p class="pub__side-note">被驳回会带原因退回，改完可以重新提交，不用重写一遍。</p>
        </PanelCard>

        <!-- 现在提交得出去吗。清单里每一条都是底下那张表单**真会拦**的规则，
             由表单自己报上来（`lib/taskPublishChecks.ts`）。 -->
        <PanelCard title="提交前">
          <ul v-if="formChecks?.length" class="pub__errors" data-testid="publish-checks">
            <li v-for="check in formChecks" :key="check.id">{{ check.text }}</li>
          </ul>
          <p v-else-if="formChecks" class="pub__ok" data-testid="publish-ok">看起来没问题。</p>
          <p v-else class="pub__wait" data-testid="publish-checks-waiting">
            表单装好之后，这里会逐条列出它现在拦着你的规则。
          </p>
          <v-btn
            block
            color="primary"
            variant="flat"
            :disabled="!formChecks || formChecks.length > 0"
            @click="submitFromChecklist"
          >
            提交审核
          </v-btn>
        </PanelCard>

        <!-- 少画，不编：这一页上没有的那几样，以及每一样的理由（见文件头那一段）。 -->
        <PanelCard title="这一页的数字从哪来">
          <ul class="pub__sources">
            <li>
              分类、话题、模板、域名组：这块板自己的
              <code>GET /spaces/{id}</code>、<code>/categories</code>、<code>/domain-groups</code>。
            </li>
            <li>「提交前」那几条：真表单自己的校验规则（<code>lib/taskPublishChecks.ts</code>），不是这一页另写的。</li>
            <li>原型的自由「标签」真库没有这一层，题目挂的是<b>话题</b> —— 表单里那枚「话题」下拉。</li>
            <li>
              附件卡上「单个文件不超过…」那个数：<code>GET /attachments/limits</code> ——
              与上传那条路拦下超限文件读的是<b>同一个上限</b>，所以卡上写的与传上去会发生的
              是同一件事。附件数量没有上限，那句不写。
            </li>
            <li v-if="quickDrafts.length">
              这份预览的模板、插图数、token：<code>POST /tasks/publish/from-pdf/preview</code> 的响应。
            </li>
          </ul>
        </PanelCard>
      </aside>
    </div>

    <!-- ============ 从 PDF 生成 ============ -->
    <div v-else class="pub__pdf">
      <!-- 回执：确认之后就地给，不跳走 —— 队列是「先审自己的、再按提交时间」排的，
           刚发的落在靠后，跳过去反而看不见自己刚做了什么。 -->
      <PanelCard v-if="receipt" title="已发布" data-testid="pdf-receipt">
        <div class="pdf__done">
          <v-icon icon="mdi-check-circle-outline" size="22" color="success" />
          <div>
            刚发的 <b>{{ receipt.count }}</b> 道题已经进了<b>待审核</b>队列 —— 解析不会绕开审核，上板还是要人看一眼。
            <div class="pdf__done-hint">
              每道题的简介里都带了出处标记
              <code>【PDF · 第 N 页】</code>
              ，审核的人在队列那一行就能看出它来自哪一页。
            </div>
          </div>
        </div>
        <ul class="pdf__made">
          <li v-for="task in receipt.tasks" :key="task.id">
            <router-link :to="{ name: 'SpaceBoardTaskDetail', params: { spaceId, taskId: String(task.id) } }">
              {{ task.name }}
            </router-link>
          </li>
        </ul>
        <div class="pdf__actions">
          <v-btn variant="text" @click="resetPdf">再解析一份 PDF</v-btn>
          <v-spacer />
          <v-btn variant="tonal" :to="{ name: BOARD_PUBLISH_DONE_ROUTE, params: { spaceId } }"
            >去「我的」看这几道</v-btn
          >
          <v-btn color="primary" variant="flat" :to="{ name: 'SpaceBoardReview', params: { spaceId } }">
            去审核队列
          </v-btn>
        </div>
      </PanelCard>

      <template v-else>
        <PanelCard
          title="上传 PDF"
          subtitle="真平台只收 PDF，单个文件最大 15MB，一次最多解析 20 道题 —— 这三条是后端写死的上限"
        >
          <v-file-input
            v-model="fileInput"
            accept=".pdf,application/pdf"
            label="上传题目 PDF"
            variant="outlined"
            density="comfortable"
            clearable
            prepend-icon=""
            hide-details="auto"
            :disabled="parsing || confirming"
            data-testid="pdf-file"
          >
            <template #prepend>
              <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
            </template>
          </v-file-input>

          <p class="pdf__hint">
            解析是只读的：读一遍 PDF、按题目模板生成草稿，<b>不会直接发出去</b>。真平台这一步要跑大模型，几秒到几十秒。
          </p>

          <!-- 结果区：真接口真的会报回来的三件事，外加一句「草稿还不是题目」。 -->
          <div v-if="parsedMeta" class="pdf__meta" data-testid="pdf-meta">
            <span data-testid="pdf-template"
              >模板：<b>{{ parsedMeta.template }}</b></span
            >
            <span data-testid="pdf-images"
              >抽出插图 <b>{{ parsedMeta.images }}</b> 张</span
            >
            <span data-testid="pdf-tokens"
              >消耗 <b>{{ parsedMeta.tokens.toLocaleString() }}</b> tokens</span
            >
            <span class="pdf__meta-warn">草稿<b>还没有成为题目</b>，确认之后才会进审核队列</span>
          </div>

          <div class="pdf__actions">
            <v-btn
              color="primary"
              variant="flat"
              :loading="parsing"
              :disabled="!selectedPdf || confirming"
              @click="parsePdf"
            >
              解析成题目草稿
            </v-btn>
            <span class="pdf__actions-note">
              模板这一版跟老页同一口径（地址栏里的 <code>?templateId=</code>，没有就是空白模板）；一次最多
              {{ MAX_DRAFTS }} 道。
            </span>
          </div>
        </PanelCard>

        <v-alert
          v-if="pdfError"
          data-testid="pdf-error"
          type="error"
          variant="tonal"
          density="comfortable"
          class="pdf__error"
        >
          {{ pdfError }}
        </v-alert>

        <PanelCard
          v-if="drafts.length"
          title="解析出的草稿"
          :subtitle="`勾选要发的 ${pickedDrafts.length} / ${drafts.length} 道，标题和题干都能就地改`"
        >
          <ul class="pdf__list" data-testid="pdf-drafts">
            <li v-for="d in drafts" :key="d.key" class="pdf__row" :class="{ 'pdf__row--off': !d.picked }">
              <v-checkbox
                v-model="d.picked"
                density="compact"
                hide-details
                class="pdf__pick"
                :aria-label="`勾选「${d.name}」`"
              />
              <div class="pdf__body">
                <v-text-field
                  v-model="d.name"
                  autocomplete="off"
                  label="标题"
                  density="compact"
                  variant="outlined"
                  hide-details
                  class="pdf__title"
                />
                <v-textarea
                  v-model="d.description"
                  autocomplete="off"
                  label="题干"
                  density="compact"
                  variant="outlined"
                  hide-details
                  rows="3"
                  auto-grow
                />
                <!-- 简介不给人改（它在确认时被加上出处标记），但发出去的就是它，
                     所以摆出来让人看得见。 -->
                <p class="pdf__intro">简介（原样发出去）：{{ d.intro }}</p>
                <div class="pdf__tags">
                  <v-chip size="x-small" label variant="text">{{ categoryLabel(d.categoryId) }}</v-chip>
                  <v-chip size="x-small" label variant="text" data-testid="draft-origin">
                    PDF · 第 {{ d.page }} 页
                  </v-chip>
                  <v-chip v-if="d.images" size="x-small" label variant="tonal" color="warning">
                    含 {{ d.images }} 张插图
                  </v-chip>
                </div>
              </div>
            </li>
          </ul>

          <p class="pdf__note-line">
            插图数是从草稿正文里的图片链接数出来的 —— 后端把抽出的插图传上存储，再把正文里的图片标记换成链接，
            所以那几张图跟着题干一起发出去。
          </p>

          <!-- 附带给领取者：原型那两颗勾。**勾只画接口真落成了文件行的那些**，
               哪一样没有就不画哪一颗、并说明为什么（见 `pdfAttachments` 的注释）。 -->
          <div class="pdf__attach" data-testid="pdf-attachments">
            <div class="pdf__attach-head">
              <b>附带给领取者</b>
              <span class="pdf__attach-note" data-testid="pdf-attach-count">
                这 {{ attachmentIdsForPdf.length }} 个文件会附在<b>每一道</b>生成出来的题上
              </span>
            </div>
            <div class="pdf__attach-row">
              <v-checkbox
                v-if="pdfAttachments?.pdf"
                v-model="attachPdf"
                label="原 PDF"
                density="compact"
                hide-details
                :disabled="confirming"
                data-testid="pdf-attach-pdf"
              />
              <v-checkbox
                v-if="pdfImages.length"
                v-model="attachImages"
                :label="`抽出的插图（${pdfImages.length} 张）`"
                density="compact"
                hide-details
                :disabled="confirming"
                data-testid="pdf-attach-images"
              />
            </div>
            <p v-if="pdfAttachments?.pdf" class="pdf__attach-file" data-testid="pdf-attach-pdf-file">
              原 PDF：{{ pdfAttachments.pdf.name }}
            </p>
            <p v-if="pdfImages.length" class="pdf__attach-file" data-testid="pdf-attach-image-files">
              插图：{{ pdfImages.map((i) => i.name).join('、') }}
            </p>
            <p v-if="!pdfAttachments?.pdf" class="pdf__attach-why" data-testid="pdf-attach-pdf-why">
              这一项<b>没画</b>「原 PDF」那颗勾：这次预览没有把 PDF 落成可附的文件（响应里没有这一项），勾了也带不走。
            </p>
            <p v-if="!pdfImages.length" class="pdf__attach-why" data-testid="pdf-attach-images-why">
              这一项<b>没画</b>「抽出的插图」那颗勾：这次没有抽到能当附件的插图（没抽到图，或抽到的图没进到任何一条草稿正文里）。
            </p>
          </div>

          <div class="pdf__actions">
            <span class="pdf__actions-note">
              确认后这 {{ pickedDrafts.length }} 道都会进<b>待审核</b>队列 —— 解析归解析，上板还是要人审。
            </span>
            <v-spacer />
            <v-btn variant="text" :disabled="confirming" @click="resetPdf">取消</v-btn>
            <v-btn
              color="primary"
              variant="flat"
              :loading="confirming"
              :disabled="!pickedDrafts.length"
              @click="confirmPdf"
            >
              确认发布 {{ pickedDrafts.length }} 道
            </v-btn>
          </div>
        </PanelCard>
      </template>
    </div>
  </div>
</template>

<style scoped lang="scss">
.pub__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.pub__head h1 {
  margin: 0;
  font-size: 1.35rem;
  font-weight: 650;
}

.pub__note {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.84rem;
}

.pub__mode {
  flex: 0 0 auto;
}

/* 手写一道：左栏是这一页自己的卡片加那两件共享机器（见文件头），右栏那几张卡。
   窄屏（左边那一列是重表单）摞成一列。 */
.pub__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.pub__main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.pub__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pub__steps {
  padding-left: 18px;
  margin: 0;
  font-size: 0.84rem;
  line-height: 1.9;
}

.pub__side-note {
  padding-top: 12px;
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 0.78rem;
  line-height: 1.7;
}

.pub__errors {
  padding-left: 18px;
  margin: 0 0 14px;
  color: rgb(var(--v-theme-error));
  font-size: 0.82rem;
  line-height: 1.8;
}

.pub__ok {
  margin: 0 0 14px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.82rem;
}

.pub__wait {
  margin: 0 0 14px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.78rem;
  line-height: 1.7;
}

.pub__sources {
  padding-left: 18px;
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
  line-height: 1.8;
}

@media (max-width: 1100px) {
  .pub__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.pub__pdf {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ---- PDF 快速发布（手写一道那一栏）---- */

.quick__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.quick__row {
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.quick__row-head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.quick__name {
  font-size: 0.86rem;
  font-weight: 600;
}

.quick__intro,
.quick__desc {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.78rem;
  line-height: 1.7;
}

/* ---- 从 PDF 生成 ---- */

.pdf__hint {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
  line-height: 1.7;
}

.pdf__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 0.78rem;
}

.pdf__meta-warn {
  color: rgb(var(--v-theme-warning));
}

.pdf__error {
  margin: 0;
}

.pdf__done {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  font-size: 0.86rem;
  line-height: 1.7;
}

.pdf__done-hint {
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}

.pdf__made {
  padding-left: 18px;
  margin: 12px 0 0;
  font-size: 0.84rem;
  line-height: 1.9;
}

.pdf__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.pdf__row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.pdf__row--off {
  opacity: 0.5;
}

.pdf__pick {
  flex: 0 0 auto;
  margin-top: 2px;
}

.pdf__body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.pdf__title :deep(input) {
  font-weight: 600;
}

.pdf__intro {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  line-height: 1.6;
}

.pdf__tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.pdf__note-line {
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__attach {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__attach-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  justify-content: space-between;
  font-size: 0.84rem;
}

.pdf__attach-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 4px;
}

.pdf__attach-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
}

.pdf__attach-file {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__attach-why {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.72);
  font-size: 0.76rem;
  line-height: 1.7;
}

.pdf__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__actions-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}
</style>
