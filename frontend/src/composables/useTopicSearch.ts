import { TagsApi } from '@/api/tags'

/**
 * 话题选择器要的两个动作：按词搜、按名新建。提问页（Ask.vue）是容器，从这里取，
 * 再作为 `searchTopics` / `createTopic` 回调交给只认 props 的 TopicSelectorView.vue，
 * 组件自己就不碰 API 层了（场景规则见 docs/manual/dev/scenes.md）。
 *
 * 只把 `@/api/tags` 的返回拆成回调要的形状（搜给候选、建给 id），调用时机与错误处理
 * 都留在组件里，和改写前 TopicSelector.vue 自己拿 TagsApi 时一模一样。
 */
export function useTopicSearch() {
  const searchTopics = async (query: string) => (await TagsApi.search(query)).topics
  const createTopic = async (name: string) => (await TagsApi.create(name)).id
  return { searchTopics, createTopic }
}
