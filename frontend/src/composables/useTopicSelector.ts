// 话题选择器背后那三件事：搜话题、建话题、把「还没建成的」话题补建成真的。
//
// 画面那一半 `components/common/TopicSelectorView.vue` 只认 props、只发事件（搜索、
// 聚焦、v-model）。取数原来长在 `components/common/TopicSelector.vue` 里，现在收在
// 这里，让 `TopicSelector.vue`（取数那一半）与提问页容器共用同一份实现 —— 页面容器
// 自己取数时不必把那二十行 `TagsApi` 逻辑再抄一遍。
import type { Topic } from '@/types'

import { ref } from 'vue'
import { debounce } from 'lodash-es'

import { TagsApi } from '@/network/api/tags'

/** 下拉里的一项。`isFakeItem` 是「本地下拉里那个还没建出来的新话题」（`id` 为 -1）。 */
export interface TopicOption {
  id: number
  name: string
  isFakeItem?: boolean
}

export function useTopicSelector(options: { defaultTopics?: () => Topic[] } = {}) {
  const items = ref<TopicOption[]>([])
  const isLoading = ref(false)

  const createTopic = async (name: string): Promise<number> => {
    try {
      isLoading.value = true
      const {
        data: { id },
      } = await TagsApi.create(name)
      return id
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 把刚选中的话题列表里那些「还没建成」的补建成真的。
   *
   * `id === -1` 是本地下拉给的假项。逐个建成真的，再交回一份**最终**列表；建失败
   * 的那个从列表里去掉。没有假项时原样返回。
   *
   * 调用方负责先把 `newTopics` 乐观地写进 v-model，再拿这里返回的最终列表覆盖一次
   * （见 `TopicSelector.vue` 与 `AskView.vue`）。
   */
  const resolveTopics = async (newTopics: Topic[]): Promise<Topic[]> => {
    const hasFake = newTopics.some((t: Topic) => (t as TopicOption).id === -1)
    if (!hasFake) return newTopics

    const finalTopics = [...newTopics]
    let changed = false

    for (let i = 0; i < finalTopics.length; i++) {
      const topic = finalTopics[i] as TopicOption
      if (topic.id === -1) {
        try {
          const newId = await createTopic(topic.name)
          // 成功：换成真话题（把 isFakeItem 抹掉）。
          finalTopics[i] = { id: newId, name: topic.name }
          changed = true
        } catch (error) {
          console.error('Create topic failed', error)
          // 建不出来就从列表里去掉。
          finalTopics.splice(i, 1)
          i--
          changed = true
        }
      }
    }

    return changed ? finalTopics : newTopics
  }

  /** 输入框里的字变了：搜已有话题；搜不到就挂一条「创建 X」的假项。 */
  const search = debounce(async (value: string) => {
    const q = value?.trim()
    if (!q) {
      items.value = [...(options.defaultTopics?.() ?? [])]
      return
    }

    try {
      isLoading.value = true
      const {
        data: { topics: result },
      } = await TagsApi.search(q)

      const next: TopicOption[] = [...result]
      // 没有一条严格同名时就补一条「创建」。
      if (!next.find((i) => i.name === q)) {
        next.push({
          id: -1,
          name: q,
          isFakeItem: true,
        })
      }

      items.value = next
    } catch (error) {
      console.error('获取话题失败:', error)
      // 出错也仍然允许创建。
      items.value = [{ id: -1, name: q, isFakeItem: true }]
    } finally {
      isLoading.value = false
    }
  }, 300)

  /** 刚聚焦、还没输入时先把默认那几项摆出来。 */
  function focus(value: string) {
    if (!value) search('')
  }

  return { items, isLoading, search, focus, resolveTopics }
}
