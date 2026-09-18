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
    /** 公开列表：私密反馈对普通用户**根本不存在**，所以它们在列表层就被滤掉了，
     *  不是靠界面不画。 */
    publicItems(state): FeedbackItem[] {
      return state.items.filter((item) => item.visibility === 'public')
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
      const base: FeedbackItem[] = this.publicItems.filter((item) => matches(item, state.query))
      if (state.tab === 'hot') {
        // 「热门」= 被支持下过阈值，按支持数排。不按浏览量：浏览是路过，支持是表态。
        return base.filter((i) => i.supports >= 5).sort((a, b) => b.supports - a.supports)
      }
      if (state.tab === 'active') {
        return base.filter((i) => ['triaging', 'planned', 'in_progress'].includes(i.status))
      }
      if (state.tab === 'resolved') return base.filter((i) => i.status === 'resolved')
      return base
    },
    /** 详情页右侧的「相关反馈」：先按标签重合度，再按支持数。 */
    related() {
      return (id: string): FeedbackItem[] => {
        const self = this.items.find((i) => i.id === id)
        if (!self) return []
        return this.publicItems
          .filter((i) => i.id !== id)
          .map((i) => ({ item: i, score: i.tags.filter((tag) => self.tags.includes(tag)).length }))
          .filter((entry) => entry.score > 0)
          .sort((a, b) => b.score - a.score || b.item.supports - a.item.supports)
          .slice(0, 3)
          .map((entry) => entry.item)
      }
    },
    tabCounts(state) {
      const base: FeedbackItem[] = this.publicItems.filter((item) => matches(item, state.query))
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

    /* ---- 中心页 ---- */

    /**
     * 支持 / 取消支持。**每条每条只算一次**：靠 supportedByMe 记，不是靠加两次
     * 数字再减 —— 那样刷新一次就会多一次。
     */
    toggleSupport(id: string) {
      const item = this.byId(id)
      if (!item) return
      // → 将来在这里发请求
      item.supportedByMe = !item.supportedByMe
      item.supports += item.supportedByMe ? 1 : -1
    },

    addComment(id: string, author: string, body: string) {
      const item = this.byId(id)
      const text = body.trim()
      if (!item || !text) return
      // → 将来在这里发请求
      const next: FeedbackComment = {
        id: `local-${Date.now()}`,
        author,
        body: text,
        createdAt: new Date().toISOString(),
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
        author: '我',
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
