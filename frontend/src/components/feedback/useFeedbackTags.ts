import { computed, ref } from 'vue'

import { cleanTags, MAX_TAGS, useFeedbackStore } from '@/stores/feedback'

// 提交表单里那一栏**标签编辑器**：芯片、正在输入的那一个词、候选，以及键盘上的那几条
// 规矩。
//
// 字段本身（`store.draft.tags`）是这一栏唯一写的东西，落盘还是走 `touchDraft()`。

export function useFeedbackTags() {
  const store = useFeedbackStore()

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

  /** 正在输入的那一个标签。它**不进草稿**：半截的词不是标签，写进草稿的话刷新回来会
   *  多出一个「登」。 */
  const tagDraft = ref('')

  /** 输入框有焦点（画焦点环用）。`:focus-within` 在带 chip 的容器上不好使 —— 焦点在
   *  里面那个 input 上，容器要的是「自己看起来被聚焦了」。 */
  const tagFocused = ref(false)

  /** 规范化在 store 里（`cleanTags`，同时也是 `toCreateBody` 用的那一个），这里先收一遍
   *  只是为了当场去掉重复的 chip：存进草稿的必须和发出去的是同一个形状。 */
  function setTags(value: string[]) {
    store.draft.tags = cleanTags(value)
    store.touchDraft()
  }

  function commitTag() {
    const raw = tagDraft.value
    if (!raw.trim()) {
      tagDraft.value = ''
      return
    }
    tagDraft.value = ''
    addTag(raw)
  }

  /** 加一个标签。到顶（`MAX_TAGS`）之后静默丢掉 —— 上限是后端的，先在这里截住，
   *  比写完几百字正文再被 422 退回来便宜得多。 */
  function addTag(tag: string) {
    if (store.draft.tags.length >= MAX_TAGS) return
    setTags([...store.draft.tags, tag])
  }

  function removeTag(tag: string) {
    setTags(store.draft.tags.filter((it) => it !== tag))
  }

  /** 输入框已经空着的时候按退格，删掉最后一个芯片 —— 芯片输入框的老规矩，没有它，
   *  想删掉刚打错的那个词只能用鼠标去点那个 16px 的叉。 */
  function onTagBackspace() {
    if (tagDraft.value || !store.draft.tags.length) return
    removeTag(store.draft.tags[store.draft.tags.length - 1])
  }

  /** 回车和逗号都是「这一个标签写完了」。逗号不能写成 `@keydown.comma` —— Vue 的按键
   *  修饰符里没有 `comma` 这个别名（自带的只有 enter/tab/delete/esc/space 和方向键），
   *  写出来是一条不生效的规则，eslint 也会当场报出来。所以按 `event.key` 自己判。
   *  两个键都要 `preventDefault`：不然回车会提交表单、逗号会跟着一起进输入框。 */
  function onTagKeydown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault()
      commitTag()
      return
    }
    if (event.key === 'Backspace') onTagBackspace()
  }

  return { tagDraft, tagFocused, tagSuggestions, addTag, removeTag, onTagKeydown }
}
