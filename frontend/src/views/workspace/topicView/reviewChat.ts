// 在频道里审阅一件任务时，「预览」里指一处、画完标注发出去，该进哪条对话。
//
// 那一栏有自己的对话：任务自己的那一条，不是频道主对话。审阅时那句「这块改一下」
// 是说给做这件任务的队友听的，落到频道里它会当成另一件活；而频道主对话那一格是另一个
// 实例（`TopicView` 里那个 `chatColumn`），两者不能混。
//
// 对话栏挂在审阅面板的 chat 槽里，点「预览」时它已经挂上了（`WorkPanel` 用 v-show
// 留着它，切走不卸载），但 Vue 把句柄交出来的时机在挂载之后，所以这里按任务 id 各记一
// 份，面板取的时候现读——面板自己不知道这条对话在哪，也不必知道。
import type { PreviewLocate, SubmitPreviewQuestion } from '@/lib/previewQuestion'
import type { ChatColumnHandle } from './shared'

import { ref } from 'vue'

export function useReviewChat() {
  const byTask = ref<Record<string, ChatColumnHandle | null>>({})

  /** 审阅那一栏的对话栏挂上（或卸下，给 null）时记一笔。
   *
   *  模板里那个函数式 ref 递进来的是组件实例：它暴露了什么由 `TopicChatColumn` 的
   *  `defineExpose` 说了算，这里按 `ChatColumnHandle` 收，形状对不上就在调用处现出来，
   *  不要在这一层吞掉一个错的形状。 */
  function hold(taskId: string, instance: unknown) {
    const handle = (instance as ChatColumnHandle | null) ?? null
    if (byTask.value[taskId] === handle) return
    byTask.value = { ...byTask.value, [taskId]: handle }
  }

  /** 预览里指出的一处：作为一条普通消息进这件任务自己的对话。 */
  function say(taskId: string, payload: PreviewLocate): boolean {
    return byTask.value[taskId]?.say(payload.message, payload.attachments) ?? false
  }

  /** 面板的结构化提问口：和「指一处」走同一条对话。 */
  function submit(taskId: string): SubmitPreviewQuestion {
    return (request) => byTask.value[taskId]?.submitQuestion(request) ?? false
  }

  return { hold, say, submit }
}
