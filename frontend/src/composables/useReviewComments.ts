// 一件任务的批注：待审阅时写在「改动」里，退回时一起交给芝士。
//
// 未发送的批注存在服务器上，只有写的人看得见，换设备、刷新都还在；退回时送出的是
// 这里列着、没被移掉的那几条。已发送的只显示上一轮的（最近一次退回带走的那批，加上
// 它们下面的回复），更早的几轮已经过去了，再摆出来只会挡住这一版的改动。
//
// 它不知道卡是哪一张：卡由 useAcceptCard 管，这里只问「现在待不待审阅」。
import type { AcceptCard } from '@/cx_types'
import type { ReviewComment, ReviewCommentDraft } from '@/types/reviewComment'

import { computed, ref, watch } from 'vue'

import { deleteReviewComment, editReviewComment, listReviewComments, writeReviewComment } from '@/api/reviewComments'
import { myHandle } from '@/me'
import { keys } from '@/query/keys'
import { fromSnapshot } from '@/query/snapshot'

export interface ReviewCommentsHost {
  taskId: () => string | null | undefined
  pendingCard: () => AcceptCard | null
}

export function useReviewComments(host: ReviewCommentsHost) {
  const me = myHandle()
  const items = ref<ReviewComment[]>([])
  // 退回那一块里被移掉的：这一次不送，留在原处还是未发送。
  const held = ref(new Set<string>())
  const busy = ref(false)

  const writable = computed(() => host.pendingCard()?.status === 'pending')
  const drafts = computed(() => items.value.filter((c) => c.state === 'draft' && c.author === me))
  const toSend = computed(() => drafts.value.filter((c) => !held.value.has(c.id)))

  // 上一轮：最近一次退回送出的那批（按送出时间认），以及回复它们的那几条。
  const lastRound = computed(() => {
    const sent = items.value.filter((c) => c.state === 'sent' && c.card_id)
    if (!sent.length) return [] as ReviewComment[]
    const latest = sent.reduce((a, b) => ((a.sent_at ?? '') >= (b.sent_at ?? '') ? a : b)).card_id
    return sent.filter((c) => c.card_id === latest)
  })
  const roundTotal = computed(() => lastRound.value.filter((c) => !c.parent_id).length)
  const roundHandled = computed(() => lastRound.value.filter((c) => !c.parent_id && c.outcome === 'handled').length)
  // 上一轮之后芝士有没有重新交过：交过才谈得上「处理了几条」。
  const roundAnswered = computed(() => {
    const card = host.pendingCard()
    const sentAt = lastRound.value[0]?.sent_at
    return !!card && !!sentAt && card.created_at > sentAt
  })

  /** 「改动」里摆出来的：自己的未发送批注，和上一轮的（连同它们下面的回复）。 */
  const shown = computed(() => {
    const roundIds = new Set(lastRound.value.map((c) => c.id))
    return items.value.filter(
      (c) =>
        (c.state === 'draft' && c.author === me) ||
        roundIds.has(c.id) ||
        (c.parent_id !== null && roundIds.has(c.parent_id))
    )
  })

  let seq = 0
  async function load() {
    const task = host.taskId()
    const mine = ++seq
    if (!task) {
      items.value = []
      return
    }
    try {
      // 只有进任务时的第一次读用房间快照里那一块（query/snapshot），之后每次都问服务器。
      const res = await fromSnapshot(keys.taskReviewComments(task), () => listReviewComments(task))
      if (mine === seq) items.value = res.comments
    } catch {
      // 读不到就先不摆：「改动」本身照常看得了。
    }
  }

  async function add(draft: ReviewCommentDraft) {
    const task = host.taskId()
    if (!task) return
    busy.value = true
    try {
      const made = await writeReviewComment(task, draft)
      items.value = [...items.value, made]
    } finally {
      busy.value = false
    }
  }

  async function edit(id: string, body: string, suggestion: string | null) {
    busy.value = true
    try {
      const made = await editReviewComment(id, body, suggestion)
      items.value = items.value.map((c) => (c.id === id ? made : c))
    } finally {
      busy.value = false
    }
  }

  async function remove(id: string) {
    busy.value = true
    try {
      await deleteReviewComment(id)
      items.value = items.value.filter((c) => c.id !== id)
      held.value.delete(id)
    } finally {
      busy.value = false
    }
  }

  function hold(id: string, on: boolean) {
    const next = new Set(held.value)
    if (on) next.add(id)
    else next.delete(id)
    held.value = next
  }

  watch(
    () => [host.taskId(), host.pendingCard()?.id ?? null] as const,
    () => {
      held.value = new Set()
      void load()
    },
    { immediate: true }
  )

  return {
    items,
    shown,
    drafts,
    toSend,
    held,
    lastRound,
    roundTotal,
    roundHandled,
    roundAnswered,
    writable,
    busy,
    me,
    load,
    add,
    edit,
    remove,
    hold,
  }
}

export type ReviewCommentsState = ReturnType<typeof useReviewComments>
