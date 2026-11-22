// 成员自己提交审阅的那一半：任务有没有「有未提交的修改」、页头给哪颗按钮，以及点开的
// 那个小面板——说明（打开时由项目的模型照改动起草，人可以直接改）、改动的文件、交给谁
// 审阅，提交。交上去走的和 AI 队友递的是同一张卡（后端 `machine.hand_in`），所以交完
// 让卡重读一次就是页头和审阅栏的新样子。
import type { Ref } from 'vue'
import type { HandInFile } from '@/api'

import { computed, ref, watch } from 'vue'

import { draftHandIn, getHandInPanel, getHandInStanding, handIn } from '@/api'
import { t } from '@/i18n'
import { acceptOffered, handInAction, noteHandedIn, savedRecently, submitsAndAdopts } from '@/lib/handIn'

export interface HandInHost {
  taskId: () => string | null | undefined
  me: Ref<string>
  /** 有一版在等审阅（卡上读得到，比问后端快）。 */
  waiting: () => boolean
  /** 看的人能改这件任务的文件：给不给提交按钮。 */
  mayHandIn: () => boolean
  /** 交上去之后：卡重读。 */
  onHandedIn: () => Promise<void> | void
  reportError: (error: unknown, fallback: string) => void
}

export function useHandIn(host: HandInHost) {
  const standing = ref(false)
  let asked = 0

  /** 再问一次平台这件任务有没有没交进去的修改。读不到就留着屏幕上那一份。 */
  async function load() {
    const task = host.taskId()
    if (!task) {
      standing.value = false
      return
    }
    const seq = ++asked
    try {
      const answer = await getHandInStanding(task)
      if (seq === asked && host.taskId() === task) standing.value = answer.unhanded
    } catch {
      // Best-effort: the header keeps what it said.
    }
  }

  const unhanded = computed(() => {
    const task = host.taskId()
    return !!task && (standing.value || savedRecently(task))
  })
  const facts = computed(() => ({ unhanded: unhanded.value, waiting: host.waiting() }))
  const action = computed(() => (host.mayHandIn() ? handInAction(facts.value) : null))
  /** 在等审阅的那一版能不能采纳。 */
  const acceptable = computed(() => acceptOffered(facts.value))

  // ---- 面板 ----
  const open = ref(false)
  const again = ref(false)
  const files = ref<HandInFile[] | null>(null)
  const subject = ref('')
  const body = ref('')
  const reviewer = ref<string | null>(null)
  const drafting = ref(false)
  const submitting = ref(false)
  // 人动过的那一格，起草回来时不再盖掉。
  let subjectTouched = false
  let bodyTouched = false
  let opened = 0

  function touchSubject(value: string) {
    subjectTouched = true
    subject.value = value
  }
  function touchBody(value: string) {
    bodyTouched = true
    body.value = value
  }

  async function openPanel() {
    const task = host.taskId()
    if (!task) return
    const seq = ++opened
    open.value = true
    files.value = null
    subject.value = ''
    body.value = ''
    reviewer.value = null
    subjectTouched = bodyTouched = false
    again.value = host.waiting()
    try {
      const panel = await getHandInPanel(task)
      if (seq !== opened) return
      files.value = panel.files
      again.value = panel.again
      reviewer.value = panel.reviewers[0] ?? null
      if (panel.again) {
        // 再提交一版：卡上那一版的说明还在，照它改。
        if (!subjectTouched) subject.value = panel.subject ?? ''
        if (!bodyTouched) body.value = panel.body ?? ''
        return
      }
    } catch (e) {
      if (seq !== opened) return
      open.value = false
      host.reportError(e, t('work.room.accept.handIn.loadFailed'))
      return
    }
    drafting.value = true
    try {
      const drafted = await draftHandIn(task)
      if (seq !== opened) return
      if (!subjectTouched) subject.value = drafted.subject
      if (!bodyTouched) body.value = drafted.body
    } catch {
      // 起草不是提交的前提：人自己写。
    } finally {
      if (seq === opened) drafting.value = false
    }
  }

  function closePanel() {
    opened++
    open.value = false
    drafting.value = false
  }

  const adopts = computed(() => submitsAndAdopts(reviewer.value, host.me.value))
  const ready = computed(() => !!subject.value.trim() && !!reviewer.value && !submitting.value)

  async function submit() {
    const task = host.taskId()
    if (!task || !ready.value) return
    submitting.value = true
    try {
      await handIn(task, {
        subject: subject.value.trim(),
        body: body.value.trim(),
        reviewer_handle: reviewer.value,
        accept: adopts.value,
      })
      noteHandedIn(task)
      standing.value = false
      closePanel()
      await host.onHandedIn()
      await load()
    } catch (e) {
      host.reportError(e, t('work.room.accept.handIn.submitFailed'))
    } finally {
      submitting.value = false
    }
  }

  watch(
    () => host.taskId(),
    () => {
      standing.value = false
      closePanel()
      void load()
    },
    { immediate: true }
  )

  return {
    unhanded,
    action,
    acceptable,
    load,
    open,
    again,
    files,
    subject,
    body,
    reviewer,
    drafting,
    submitting,
    adopts,
    ready,
    touchSubject,
    touchBody,
    openPanel,
    closePanel,
    submit,
  }
}

export type HandInState = ReturnType<typeof useHandIn>
