import type { InjectionKey } from 'vue'

// 发题页右栏那张「提交前」清单的规则表，以及表单把它当前的状态交上来的那条注入键。
//
// 这张卡**不自己发明规则**：下面每一条都是真发题表单实际会拦的条件，逐条对着
// `components/tasks/TaskForm.vue` 里 `useForm({ validationSchema: toTypedSchema(z.object({…})) })`
// 那一份 zod schema 抄下来的 —— 那份 schema 就是 `handleSubmit` 在本地挡住提交的判据，
// 也就是「点了提交会不会发出去请求」这件事唯一的真相。每条规则都写了它出自哪一行，
// 改那边请同时改这里（`taskPublishChecks.spec.ts` 里有一条用例拿真表单核对过其中
// 会变的那些）。
//
// 后端 `backend/app/api/routes/tasks.py` 建题那条路（`CreateTaskRequest` + `_create_task_entity`）
// 另有几道校验，**故意没有列进来**，因为它们从这张表单出发碰不到：
//
// - `space` 必须已过审（`tasks.py:1174-1176`）与「发题人得是这块板的成员」
//   （`may_publish_in_space`，`tasks.py:1184-1187`）：读不到的空间/不是成员根本打不开
//   这一页（外壳把 `loadFailed` 换掉整页），表单也就无从提交。
// - 分类必须属于这块板、未归档、未删除（`_validate_and_get_category_id`，`tasks.py:899-936`）：
//   表单那枚下拉里只有这块板**未归档**的分类（`PublishTask.vue` 的 `activeCategories`），
//   选不出别的。
// - 「minTeamSize / maxTeamSize 只对 TEAM 题有效」（`tasks.py:1150-1153`）：表单只在
//   「团队」时把这两项发出去（`TaskForm.vue` 的 `submitFormData`），发不出违规形状。
//
// 反过来，**原型里有一条真表单并不拦**，所以这里没有：「题干至少 10 个字」——真表单的
// zod schema 里根本没有 `description` 这一项，空着题干照样发得出去。
//
// 另有一条**不算表单校验、因此不列**的：附件还在上传时点提交会被老页挡下来
// （`views/spaces/detail/PublishTask.vue:228-231`，`attachmentUploading` → 一句 toast 后
// 返回）。它是「等一等」的瞬时状态，不是表单字段的规则，而且这份状态在老页内部、这一层
// 读不到；发题页两条路共用它，改它又碰到 PDF 那条路的文件，所以这里不碰。

/** 清单上的一行。`v-for` 的 key 与测试都认 `id`，人看的是 `text`。 */
export interface PublishCheck {
  id: string
  text: string
}

/**
 * 表单交上来的东西：它现在**拦着你的**那几条（没拦着的报空数组），加上它自己的提交。
 *
 * 由挂着这一页的外壳 provide（`board/pages/TaskPublish.vue`），表单 inject。别处不
 * provide 就整块不生效 —— 老树那边（`views/spaces/detail/PublishTask.vue` 被空间壳
 * 直接挂着）一个字都不用改。
 */
export interface PublishChecksSink {
  /** 表单每变一次报一次。空数组 = 现在提交得出去。 */
  report(checks: PublishCheck[]): void
  /** 表单把自己的提交交上来；`null` = 表单没挂（或已经卸了）。卡片那颗按钮走的就是它。 */
  handOverSubmit(submit: (() => void) | null): void
}

export const PUBLISH_CHECKS_SINK: InjectionKey<PublishChecksSink> = Symbol('publish-checks-sink')

/** 表单里被这份清单读到的那些格子。`TaskForm.vue` 直接把 `useForm` 的 `values` 递进来。 */
export interface PublishChecksValues {
  name?: unknown
  submitterType?: unknown
  rank?: unknown
  categoryId?: unknown
  defaultDeadline?: unknown
  minTeamSize?: unknown
  maxTeamSize?: unknown
  participantLimit?: unknown
  videoUrl?: unknown
  /** 表单里还有别的格子（题干、话题、附件、域名组……）。规则表**一条都不读**它们，
   *  但 `useForm` 那一整份 `values` 是要原样递进来的，所以这里收得下。 */
  [field: string]: unknown
}

interface PublishCheckRule extends PublishCheck {
  /** 这条出自哪个文件的哪一行 —— 与 `text` 一样是这份规则表的一部分。 */
  source: string
  /** 现在**违反**了没有。与 zod 同一口径：不 trim、不猜、只看它真怎么算。 */
  violated(values: PublishChecksValues): boolean
}

/** `z.number()` 在字段被清空时会拿到 `''`，所以「是不是整数」得自己判。 */
function isInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value)
}

/** zod 里 `z.number().int().min(1).optional()` 那三兄弟的同一口径：不填算过。 */
function optionalMinOne(value: unknown): boolean {
  return value === undefined || value === null || (isInt(value) && value >= 1)
}

const RULES: PublishCheckRule[] = [
  {
    id: 'name',
    text: '标题：必填，最多 100 个字',
    // TaskForm.vue:736 `name: z.string().min(1).max(100)`
    // （后端 `CreateTaskRequest.name: str` 只要求有这一项，100 字是前端这一层的事）
    source: 'components/tasks/TaskForm.vue:736',
    violated: (values) => typeof values.name !== 'string' || values.name.length < 1 || values.name.length > 100,
  },
  {
    id: 'submitterType',
    text: '参与者类型：必选一个（个人 / 团队）',
    // TaskForm.vue:737 `submitterType: z.enum(['USER', 'TEAM'])`
    source: 'components/tasks/TaskForm.vue:737',
    violated: (values) => values.submitterType !== 'USER' && values.submitterType !== 'TEAM',
  },
  {
    id: 'rank',
    text: '题目难度：必选一个（初级 / 中级 / 高级）',
    // TaskForm.vue:741 `rank: z.number().int().min(1).max(3)`
    source: 'components/tasks/TaskForm.vue:741',
    violated: (values) => !(isInt(values.rank) && values.rank >= 1 && values.rank <= 3),
  },
  {
    id: 'categoryId',
    text: '所属分类：必选一个（这块板的分类）',
    // TaskForm.vue:743 `categoryId: z.number().int().min(1, '请选择所属分类')`
    // 后端按这个 id 找分类，找不到/已归档/已删除都会退回来（tasks.py:899-936）。
    source: 'components/tasks/TaskForm.vue:743',
    violated: (values) => !(isInt(values.categoryId) && values.categoryId >= 1),
  },
  {
    id: 'defaultDeadline',
    text: '领取后默认天数：要填一个整数',
    // TaskForm.vue:740 `defaultDeadline: z.number().int().default(30)`
    // （不填走默认 30；清空这个输入框拿到的是 `''`，不是整数，表单会拦）
    source: 'components/tasks/TaskForm.vue:740',
    violated: (values) => values.defaultDeadline !== undefined && !isInt(values.defaultDeadline),
  },
  {
    id: 'teamSize',
    text: '队伍人数：最小 1 人，且上限不能小于下限（选「团队」时这两项才发出去）',
    // TaskForm.vue:744-745 `minTeamSize/maxTeamSize: z.number().int().min(1).optional()`
    // 与 TaskForm.vue:767-770 那条 `.refine(max >= min)`（口径是「两个都填了才比」，
    // 0 当没填）—— 表单只在「团队」时把这两项发给后端，但 zod 这一层是不分类型的。
    source: 'components/tasks/TaskForm.vue:744-745, 767-770',
    violated: (values) => {
      if (!optionalMinOne(values.minTeamSize) || !optionalMinOne(values.maxTeamSize)) return true
      const min = values.minTeamSize
      const max = values.maxTeamSize
      if (!min || !max) return false
      return (max as number) < (min as number)
    },
  },
  {
    id: 'participantLimit',
    text: '参与人数上限：不填 = 不限；填了就得是 ≥ 1 的整数（删空会被当成填错）',
    // TaskForm.vue:747 `participantLimit: z.number().int().min(1).optional().nullable()`
    // 初始值是 `null`（TaskForm.vue:789），所以「不填 = 不限」是真的；但把输入框里的
    // 数字删掉拿到的是 `''`（`.number` 对空串原样留着），那既不是 `null` 也不是整数，
    // 表单照样拦 —— 所以这句话里带上了那个坑。`TaskFormPublishChecks.test.ts` 里有一条
    // 用例拿真表单的提交结果核对过这两件事。
    source: 'components/tasks/TaskForm.vue:747',
    violated: (values) => !optionalMinOne(values.participantLimit),
  },
  {
    id: 'videoUrl',
    text: '讲解视频链接：要么不填，要么是一个 https 开头的地址',
    // TaskForm.vue:751-763 `.refine(v => !v || new URL(v).protocol === 'https:', { message: '请输入有效的 HTTPS 链接' })`
    // （表单后面还会为「不是 B 站链接」问一句要不要继续存 —— 那是**问一句**，不是拦，
    //  所以不在这张清单里）
    source: 'components/tasks/TaskForm.vue:751-763',
    violated: (values) => {
      const url = values.videoUrl
      if (url === undefined || url === null || url === '') return false
      if (typeof url !== 'string') return true
      try {
        return new URL(url).protocol !== 'https:'
      } catch {
        return true
      }
    },
  },
]

/** 现在拦着你的那几条，按表单里从上到下的次序。全过就是空数组。 */
export function evaluatePublishChecks(values: PublishChecksValues): PublishCheck[] {
  return RULES.filter((rule) => rule.violated(values)).map((rule) => ({ id: rule.id, text: rule.text }))
}
