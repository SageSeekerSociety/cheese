/** 发出去的消息 @ 了不在这个话题里的人：告诉发的人他们收不到，能管名册的人顺手
 * 把他们拉进来（Slack 的做法）。
 *
 * 为什么是发出去**之后**才说：@ 一个不在场的人是允许的（候选里挂着「不在话题中」
 * 照样挑得中），发之前拦一下等于把一件合法的事变成一道确认题。发完了再说一句
 * 「他们不会收到通知」，要不要补救由人决定。
 *
 * 拉人走的是名册抽屉「添加成员」那一条（`addTopicMember`），不另写一条：它写成功
 * 会通知对话栏的名册重拉（`lib/topicRosterChanges.ts`），@ 候选跟着就对了。
 *
 * 不自动拉：拉不拉是产品上还没定的事，这里只给一颗按钮。
 */
import type { Topic } from '../cx_types'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { computed, ref, watch } from 'vue'

import { addTopicMember } from '../api'
import { t } from '../i18n'
import { mentionsHandle } from '../lib/expandMentions'

/** 名册上能加人的角色，和名册抽屉（`TopicMembers.vue` 的 canManage）同一条规矩。 */
const MANAGER_ROLES = new Set(['owner', 'admin'])

export function useOutsideMentionPrompt(deps: {
  topic: () => Topic | null
  mentionPool: () => MentionPoolEntry[]
  /** 自己的登录 handle。 */
  me: () => string
}) {
  /** 刚才那条消息 @ 到、却不在这个话题里的人。空 = 不提示。 */
  const outside = ref<{ handle: string; label: string }[]>([])
  const busy = ref(false)
  const error = ref('')

  // 自己在这个话题名册上的角色。名册没到、或者自己不在名册上，都当作管不了：
  // 那颗按钮按下去后端会拒，不如不给。
  const canAdd = computed(() => {
    const mine = deps.mentionPool().find((m) => m.handle === deps.me() && !m.outsideTopic)
    return !!mine?.role && MANAGER_ROLES.has(mine.role)
  })

  /** 一条消息发出去了（`expanded` 是展开成 `<@handle>` 之后的正文）。 */
  function noteSent(expanded: string) {
    error.value = ''
    outside.value = deps
      .mentionPool()
      .filter((m) => m.outsideTopic && mentionsHandle(expanded, m.handle))
      .map((m) => ({ handle: m.handle, label: m.label }))
  }

  function dismiss() {
    outside.value = []
    error.value = ''
  }

  /** 把他们拉进这个话题。一个一个加：中途失败时，加上了的就不再列着。 */
  async function add() {
    const topicId = deps.topic()?.id
    if (!topicId || busy.value || !canAdd.value) return
    busy.value = true
    error.value = ''
    try {
      for (const person of [...outside.value]) {
        await addTopicMember(topicId, person.handle, 'member')
        outside.value = outside.value.filter((p) => p.handle !== person.handle)
      }
    } catch (e) {
      error.value = e instanceof Error && e.message ? e.message : t('work.room.roster.failed')
    } finally {
      busy.value = false
    }
  }

  // 换了话题，上一个话题的提示不带过来。
  watch(() => deps.topic()?.id, dismiss)

  const names = computed(() => outside.value.map((p) => p.label).join(t('work.room.mention.nameJoiner')))

  return { outside, names, canAdd, busy, error, noteSent, add, dismiss }
}
