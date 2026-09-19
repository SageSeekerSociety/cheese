/**
 * stores/feedback.ts — 反馈原型的**全部状态**。
 *
 * 这一层存在的理由只有一个：让「接真接口」将来只改这一个文件。页面和组件一律
 * 通过 action / getter 读写，谁都不许直接摸 mock 数组 —— 那样等接口来了，散在
 * 八个组件里的「本地怎么改」就得一个一个找出来（而且找不干净）。
 *
 * 现在没有网络请求，动作都是同步改本地数组；**加 await 的位置已经留好了**
 * （每个 action 里那一行 `// → 将来在这里发请求`），所以接到真接口时改的是函数体，
 * 不是调用方的写法。
 *
 * 角色（user / admin）也在这里：需求说「不实现权限后端，只需要通过 mock role 展示
 * user/admin 两套界面」。所以它就是一个能切的开关，**不是**一道权限门 —— 它决定
 * 界面显示什么，不决定数据能不能读到。真到了接后端那一步，这个字段就该被删掉，
 * 权限判断回到服务端。
 *
 * 「私密反馈谁能看」这一条刻意**不**走那个开关，见下面的 me / visibles：私密不等于
 * 「只有管理员」，提交者本人也看得见自己提的东西。原型阶段它在客户端算，接后端之后
 * 它是服务端 WHERE 子句里的一项 —— 前端这层要整个删掉，而不是留着当第二道门。
 */

import type {
  FeedbackComment,
  FeedbackItem,
  FeedbackKind,
  FeedbackRole,
  FeedbackStatus,
  FeedbackVisibility,
} from '@/lib/feedbackMock'

import { defineStore } from 'pinia'

import { seedFeedback } from '@/lib/feedbackMock'
import { myHandle } from '@/me'

/**
 * 原型专用的假延迟，单位毫秒。
 *
 * 它存在的唯一理由是让骨架屏在预览里看得见 —— 数据本来就在内存里，不拖一下的话
 * 骨架只会闪一帧，评审根本看不见自己要看的东西。**接真接口时这个常量连同它下面
 * 那个 setTimeout 一起去掉**，那时 loading 由请求本身决定。
 */
const LOAD_DEMO_MS = 420

/** 角色存 localStorage：预览时在两个页面之间跳，来回切换会烦人。 */
const ROLE_KEY = 'cheese.feedback.mockRole'

function readRole(): FeedbackRole {
  try {
    return localStorage.getItem(ROLE_KEY) === 'admin' ? 'admin' : 'user'
  } catch {
    return 'user'
  }
}

/** 提交抽屉里那一份表单。提交流程有两处（中心页的按钮、会话里的 Agent 卡片），
 *  它们打开的是同一个抽屉，所以「抽屉开着、内容是什么」放在 store 里而不是某个
 *  页面的 ref 上 —— 否则 AgentFeedbackCard 得把抽屉再实现一遍。 */
export interface FeedbackDraft {
  kind: FeedbackKind
  title: string
  body: string
  /** 附件只做 UI：记名字用于展示，不上传、不产生文件。 */
  attachments: string[]
  attachLogs: boolean
  visibility: FeedbackVisibility
  /** 由 Agent 发现的那条带过来的现场，提交时一并附上。 */
  fromAgent?: {
    whatHappened: string
    repro: string
    evidence: string
    sessionId?: string
    environment?: string
  }
}

function emptyDraft(): FeedbackDraft {
  return {
    kind: 'bug',
    title: '',
    body: '',
    attachments: [],
    attachLogs: false,
    visibility: 'public',
  }
}

/** 搜索匹配：标题、描述、标签、编号都要能搜到 —— 只搜标题的话，记得住现象记不住
 *  标题的人就搜不着。 */
function matches(item: FeedbackItem, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [item.id, item.title, item.summary, ...item.tags].some((field) => field.toLowerCase().includes(q))
}

export type FeedbackTab = 'all' | 'hot' | 'active' | 'resolved'
export type AdminTab = 'public' | 'private' | 'agent' | 'security'

export const useFeedbackStore = defineStore('feedback', {
  state: () => ({
    items: seedFeedback(),
    /**
     * 是否正在加载。**不分页面**：中心页和详情页不会同时挂着（路由是替换，不是叠加），
     * 而「数据在不在路上」本来就是一份数据的事。真接口来了这里会拆成按资源的若干份，
     * 那时列表和详情各等各的。
     */
    loading: false,
    role: readRole(),
    query: '',
    tab: 'all' as FeedbackTab,
    adminTab: 'public' as AdminTab,
    /* 抽屉：open 控制显隐，draft 是那一份表单。两处入口共用。 */
    submitOpen: false,
    draft: emptyDraft(),
    /** 提交成功后给页面的一个一次性提示（v-snackbar 读它）。 */
    lastSubmittedId: null as string | null,
  }),

  getters: {
    /**
     * 当前用户是谁 —— 「我能不能看到这条私密反馈」的**唯一**判据。
     *
     * 取真身的登录身份；原型里没登录（预览就是这种情况）时退化成「我」，本地提交的
     * 那条正好用同一个值，于是「提完能看见」这条路径在预览里是走得通的。
     */
    me(): string {
      return myHandle() || '我'
    },
    /** 这条是不是我提的。判断私密可见性时用它，不要各组件各写一遍 author 比较。 */
    isMine() {
      return (item: FeedbackItem): boolean => item.author === this.me
    },
    /**
     * 我**能看见**的反馈：公开的 ∪ 我自己提交的（含私密）。
     *
     * 私密不等于「只有管理员能看见」：提交者本人必须看得见自己提的东西，否则他提完
     * 就再也找不到那一条，也没法回来补充说明——而那正是私密反馈最常见的用法。
     * 对应的后端查询就是 `WHERE visibility = 'public' OR author_id = :me`。
     *
     * 这一条**每次实时算**，不烘进数据也不缓存结果：它是**读的时候**才成立的事实，
     * 一旦存下来，管理员改了可见性、或者换了登录身份，那份缓存立刻就在说谎。将来接
     * 后端时它是 WHERE 子句里的一项，前端这层整个删掉。
     */
    visibles(state): FeedbackItem[] {
      const me = this.me
      return state.items.filter((item) => item.visibility === 'public' || item.author === me)
    },
    byId:
      (state) =>
      (id: string): FeedbackItem | undefined =>
        state.items.find((item) => item.id === id),
    /** 管理员列表按 Tab 过滤。安全问题独立成栏，且不混进「私密反馈」。 */
    adminItems(state): FeedbackItem[] {
      const visible = state.items
      if (state.adminTab === 'public') return visible.filter((i) => i.visibility === 'public')
      if (state.adminTab === 'private') return visible.filter((i) => i.visibility === 'private' && !i.security)
      if (state.adminTab === 'agent') return visible.filter((i) => i.source === 'agent')
      return visible.filter((i) => i.security)
    },
    /** 中心页主区域：Tab + 搜索。 */
    visibleItems(state): FeedbackItem[] {
      const base: FeedbackItem[] = this.visibles.filter((item) => matches(item, state.query))
      if (state.tab === 'hot') {
        // 「热门」= 被支持下过阈值，按支持数排。不按浏览量：浏览是路过，支持是表态。
        return base.filter((i) => i.supports >= 5).sort((a, b) => b.supports - a.supports)
      }
      if (state.tab === 'active') {
        return base.filter((i) => ['triaging', 'planned', 'in_progress'].includes(i.status))
      }
      if (state.tab === 'resolved') return base.filter((i) => i.status === 'resolved')
      // 「全部」里已解决的**沉底，不是滤掉**：它们仍然要搜得到——「这个 bug 到底修了
      // 没有」是这里最常发生的一次查找，直接滤掉等于让人以为它不存在。但它们也不该
      // 占着列表最上面那几屏，所以往后放。Array#sort 是稳定的，同类之间保持原顺序。
      return [...base].sort((a, b) => Number(a.status === 'resolved') - Number(b.status === 'resolved'))
    },
    /** 详情页右侧的「相关反馈」：先按标签重合度，再按支持数。 */
    related() {
      return (id: string): FeedbackItem[] => {
        const self = this.items.find((i) => i.id === id)
        if (!self) return []
        return this.visibles
          .filter((i) => i.id !== id)
          .map((i) => ({ item: i, score: i.tags.filter((tag) => self.tags.includes(tag)).length }))
          .filter((entry) => entry.score > 0)
          .sort((a, b) => b.score - a.score || b.item.supports - a.item.supports)
          .slice(0, 3)
          .map((entry) => entry.item)
      }
    },
    tabCounts(state) {
      const base: FeedbackItem[] = this.visibles.filter((item) => matches(item, state.query))
      return {
        all: base.length,
        hot: base.filter((i) => i.supports >= 5).length,
        active: base.filter((i) => ['triaging', 'planned', 'in_progress'].includes(i.status)).length,
        resolved: base.filter((i) => i.status === 'resolved').length,
      }
    },
    adminTabCounts(state) {
      return {
        public: state.items.filter((i) => i.visibility === 'public').length,
        private: state.items.filter((i) => i.visibility === 'private' && !i.security).length,
        agent: state.items.filter((i) => i.source === 'agent').length,
        security: state.items.filter((i) => i.security).length,
      }
    },
  },

  actions: {
    setRole(role: FeedbackRole) {
      this.role = role
      try {
        localStorage.setItem(ROLE_KEY, role)
      } catch {
        // 存不了就只影响下次打开，不影响这一轮。
      }
    },

    /* ---- 加载 ---- */

    /**
     * 页面挂载时调一次，用来把「数据还没到」这段时间显式地走一遍（页面据此画骨架）。
     *
     * 每一次都重新走，**不做「已经加载过就跳过」的缓存**：这一轮要评审的正是骨架到
     * 内容的过渡，缓存会让它只在第一次出现。真接口接上之后要不要缓存是另一个决定，
     * 那时这里就是一句 `await fetchList()`。
     */
    async load(): Promise<void> {
      this.loading = true
      await new Promise((resolve) => setTimeout(resolve, LOAD_DEMO_MS))
      this.loading = false
    },

    /* ---- 中心页 ---- */

    /**
     * 支持 / 取消支持。**每条每条只算一次**：靠 supportedByMe 记，不是靠加两次
     * 数字再减 —— 那样刷新一次就会多一次。
     *
     * 私密反馈**不能支持**：支持是一个公开表态（它决定「热门」怎么排、管理员先看
     * 哪条），而私密反馈本来就不该让任何人知道它存在。门口这里也拦一道，不只在
     * 界面上不画按钮 —— 按钮是容易漏的，数据层是判据。
     */
    toggleSupport(id: string) {
      const item = this.byId(id)
      if (!item || item.visibility === 'private') return
      // → 将来在这里发请求
      item.supportedByMe = !item.supportedByMe
      item.supports += item.supportedByMe ? 1 : -1
    },

    /**
     * 发一条评论。`parentId` 有值就是回复。
     *
     * **只有两层，规则收在这里**：回复一条回复时，挂到那条回复所属的顶层评论上，
     * 而不是把它自己当父级 —— 三个评论布局里只有一个在乎层级，把这条规则写在数据层
     * 就不会出现「甲平铺、丙嵌套、两边的父子关系还不一样」。
     */
    addComment(id: string, author: string, body: string, parentId?: string) {
      const item = this.byId(id)
      const text = body.trim()
      if (!item || !text) return
      // → 将来在这里发请求
      const parent = parentId ? item.comments.find((c) => c.id === parentId) : undefined
      const topId = parent?.parentId ?? parent?.id
      const next: FeedbackComment = {
        id: `local-${Date.now()}`,
        author,
        body: text,
        createdAt: new Date().toISOString(),
        ...(topId ? { parentId: topId } : {}),
      }
      item.comments.push(next)
    },

    /* ---- 提交抽屉 ---- */

    openSubmit(preset: Partial<FeedbackDraft> = {}) {
      this.draft = { ...emptyDraft(), ...preset }
      this.submitOpen = true
    },

    closeSubmit() {
      this.submitOpen = false
      this.lastSubmittedId = null
    },

    /** 附件选择只记名字。**不上传**：这一轮的验收标准就是「上传附件只做 UI」。 */
    addAttachment(name: string) {
      if (!name || this.draft.attachments.includes(name)) return
      this.draft.attachments.push(name)
    },

    removeAttachment(name: string) {
      this.draft.attachments = this.draft.attachments.filter((n) => n !== name)
    },

    /** 提交。返回新条目的 id，调用方据此跳转。 */
    submit(): string | null {
      const draft = this.draft
      const title = draft.title.trim()
      if (!title) return null
      // → 将来在这里发请求
      // 编号取现有最大值 +1，**不是**「条数 +1」：种子里删掉一条之后，条数会撞上
      // 一个已经在用的编号。
      const max = this.items.reduce((acc, i) => Math.max(acc, Number(i.id.replace(/\D/g, '')) || 0), 0)
      const id = `FB-${max + 1}`
      const now = new Date().toISOString()
      const item: FeedbackItem = {
        id,
        title,
        summary: draft.body.trim().split('\n')[0].slice(0, 60) || title,
        kind: draft.kind,
        status: 'received',
        visibility: draft.visibility,
        // Agent 带过来的那条，来源就该是 Agent —— 人只是点了「提交」。
        source: draft.fromAgent ? 'agent' : 'user',
        priority: 'normal',
        // 用**真身**（没登录时才是「我」）：作者字段是可见性判断的输入，
        // 写死成「我」的话，登录之后自己提的私密反馈会立刻从列表里消失。
        author: this.me,
        createdAt: now,
        supports: 0,
        supportedByMe: false,
        views: 0,
        tags: draft.attachLogs ? ['会话信息'] : [],
        comments: [],
        problem: draft.body.trim(),
        why: '',
        expectation: '',
        whatHappened: draft.fromAgent?.whatHappened,
        repro: draft.fromAgent?.repro,
        evidence: draft.fromAgent?.evidence,
        sessionId: draft.fromAgent?.sessionId,
        environment: draft.fromAgent?.environment,
        logs: draft.attachLogs ? '（原型：日志只在界面上标记为「已附带」，没有真的采集）' : undefined,
        timeline: [{ status: 'received', at: now, by: '我' }],
      }
      // 新提交的排最前：列表是按时间读的，塞在末尾等于「我提交的东西不见了」。
      this.items.unshift(item)
      this.submitOpen = false
      this.lastSubmittedId = id
      this.draft = emptyDraft()
      return id
    },

    /* ---- 管理员侧 ---- */

    /** 推进状态：改状态的同时补一条 timeline —— 只改状态不写时间线，右侧那根
     *  Timeline 就会和卡片上的状态词对不上。 */
    setStatus(id: string, status: FeedbackStatus, by = '管理员') {
      const item = this.byId(id)
      if (!item || item.status === status) return
      item.status = status
      item.timeline.push({ status, at: new Date().toISOString(), by })
    },

    setPriority(id: string, priority: FeedbackItem['priority']) {
      const item = this.byId(id)
      if (item) item.priority = priority
    },

    assign(id: string, handle: string | null) {
      const item = this.byId(id)
      if (item) item.assignee = handle
    },

    /** 内部备注。需求明确：**不给「私密转公开」的按钮** —— 所以这里也就没有
     *  改 visibility 的动作。 */
    setInternalNote(id: string, note: string) {
      const item = this.byId(id)
      if (item) item.internalNote = note
    },
  },
})
