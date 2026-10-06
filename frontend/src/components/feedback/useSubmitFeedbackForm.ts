import type { FeedbackKind } from '@/cx_types'
import type { FeedbackDraft } from '@/stores/feedback'

import { computed } from 'vue'

import { cleanTags, EXPECTATION_KINDS, MAX_TAGS, REPRO_KINDS, useFeedbackStore } from '@/stores/feedback'

/**
 * 提交反馈那张表单**取数的那一半**：草稿怎么读、改了哪一栏怎么落盘、标签怎么规范化、
 * 提交时发什么请求。
 *
 * 为什么单独一份：这张表单有三个入口，而字段只有一份 ——
 *
 *   * 页面壳 `views/feedback/FeedbackSubmitPage.vue`（`/feedback/new`）。反馈中心和
 *     「我的反馈」都到这一页。一行字要填半天的时候，页面比浮层少一层「我在哪、关掉
 *     会不会丢」的疑问；刷新之后人也还停在表单上（路由就是状态，而抽屉/弹窗刷新后
 *     会关掉，草稿虽在盘上、界面却没了）。
 *   * 对话框壳 `SubmitFeedbackForm.vue` —— 会话里那张 agent 提案卡走它。**只有它不
 *     跳页**：从对话里跳走会把「我刚看到的那张卡」留在身后，而卡片提交完要就地翻成
 *     一张凭证（`AgentFeedbackCard` 的 `submitted` 是组件内的 ref，跳页必丢）。
 *
 * 两个壳各接一遍 store 的话，「改一栏就落盘」这种规矩必然只会出现在其中一份里，
 * 而那种偏差的表现是「刷新回来少了一个字符」—— 界面上什么看起来都没坏。所以接线的
 * 那一半收在这里，两个壳各自只做「把值递下去、把事件接回来」。
 *
 * 画面在 `SubmitFeedbackFormView.vue`（只吃 props、只发事件），所以页面的视图那一半
 * 不必为了表单碰 store。
 */
export function useSubmitFeedbackForm() {
  const store = useFeedbackStore()

  /** 正在填的这一份。视图只读它，改动一律走 `patch`。 */
  const draft = computed(() => store.draft)

  /** 词表来自服务端；meta 还没到时用这三个 —— 它们是 `FeedbackKind` 的全部取值。 */
  const kinds = computed<FeedbackKind[]>(() => store.meta?.kinds ?? ['bug', 'suggestion', 'other'])

  /** 手上这份是刚从盘上捞回来的。 */
  const restoredNotice = computed(() => store.draftRestored)

  // 按类型出现的两栏。**表单项和请求体问的是同一个问题**（`toCreateBody` 用的就是这两
  // 个常量），所以这里也读它们，不在这一层再写一遍 `kind === 'bug'`：两处各写一遍
  // 的话，改口径时表单和请求体会漂开，而漂开的方向恰好是最难看出来的那一种 —— 屏幕上
  // 问了、提交上去却没有。
  const askRepro = computed(() => REPRO_KINDS.includes(store.draft.kind))
  const askExpectation = computed(() => EXPECTATION_KINDS.includes(store.draft.kind))

  /** 提交按钮能不能按。查的是**同一个门槛**（`store.submit()` 里也查两栏），按钮
   *  disabled 不是替代品 —— 提交这个动作还有别的调用点。 */
  const canSubmit = computed(() => !!store.draft.title.trim() && !!store.draft.body.trim() && !store.submitting)

  /** 标签候选：**从已经加载到的那两份列表里**出现的标签汇总。数据现成，不引新依赖、
   *  也不为它加一个后端接口 —— 候选只是省打字，拿不到候选时这个输入框照样能用。
   *  已经填在表单里的那些要排掉，否则选完一次它还会再提一次同一个词。 */
  const tagSuggestions = computed(() => {
    const seen = new Set<string>()
    for (const item of [...store.items, ...store.mineItems]) {
      for (const tag of item.tags) seen.add(tag)
    }
    for (const tag of store.draft.tags) seen.delete(tag)
    return [...seen].sort()
  })

  const submitting = computed(() => store.submitting)
  /** 服务端的原话（412 的「已经办完了」之类也走这里）。 */
  const error = computed(() => store.error)

  /** 视图改了哪几栏就报哪几栏：一起写回草稿，然后落盘 —— 不落盘的话，选完类型刷新回来
   *  又变回 Bug，而这一页的其余部分（正文那一栏的问法）是跟着类型变的。 */
  function patch(values: Partial<FeedbackDraft>): void {
    Object.assign(store.draft, values)
    store.touchDraft()
  }

  /** 规范化在 store 里（`cleanTags`，同时也是 `toCreateBody` 用的那一个），这里先收一遍
   *  只是为了当场去掉重复的 chip：存进草稿的必须和发出去的是同一个形状。 */
  function setTags(value: string[]): void {
    store.draft.tags = cleanTags(value)
    store.touchDraft()
  }

  /** 加一个标签。到顶（`MAX_TAGS`）之后静默丢掉 —— 上限是后端的，先在这里截住，
   *  比写完几百字正文再被 422 退回来便宜得多。 */
  function addTag(tag: string): void {
    if (store.draft.tags.length >= MAX_TAGS) return
    setTags([...store.draft.tags, tag])
  }

  function removeTag(tag: string): void {
    setTags(store.draft.tags.filter((it) => it !== tag))
  }

  /** 「丢弃草稿」——盘上两份槽位一起抹掉，见 store 里那个同名 action。 */
  function discardDraft(): void {
    store.discardDraft()
  }

  /** 提交。成了交回新那条的 id，失败交回 null：失败时**什么都不关** —— `store.error`
   *  是服务端的原话，它就在表单上那块提示里，而人写的那几百字还在。 */
  function submit(): Promise<string | null> {
    return store.submit()
  }

  return {
    draft,
    kinds,
    restoredNotice,
    askRepro,
    askExpectation,
    canSubmit,
    tagSuggestions,
    submitting,
    error,
    patch,
    addTag,
    removeTag,
    discardDraft,
    submit,
  }
}
