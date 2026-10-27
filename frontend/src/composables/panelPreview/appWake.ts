// 应用睡着时的那两件事：最近一次成功加载的静态快照，和「唤醒并重启应用」的分步进度。
//
// 快照由帧里的运行时桥拍（`usePreviewFrames` 的 `onSnapshot`），这一层把它存到平台上
// （房间里谁打开都看得到），应用睡着时再读回来。唤醒交给声明这个应用的队友：平台不知道
// 应用是怎么起的，起一轮让它去重启；这一层在等的时候每两秒问一次走到哪一步，应用答了就
// 重新载入预览，回到能点的页面。
import type { FrameSnapshot, PreviewFrame } from '../usePreviewFrames'
import type { PreviewState } from './previewState'
import type { PanelPreviewProps } from './types'

import { computed, onScopeDispose, ref, watch } from 'vue'

import { getPreviewSnapshot, getPreviewWakeStatus, putPreviewSnapshot, wakePreviewApp } from '../../api'
import { t } from '../../i18n'

/** 唤醒进度里的一步。 */
export interface AppWakeStep {
  key: 'machine' | 'tunnel' | 'agent' | 'port'
  label: string
  state: 'done' | 'now' | 'todo'
}

/** 等多久还没起来就认输、把按钮还回去：机器冷启动实测要两分多钟，再给一些余量。 */
export const WAKE_BUDGET_MS = 180_000
const WAKE_POLL_MS = 2_000

/** 快照是多久以前留的，说成一句话。 */
export function snapshotAge(takenAtSeconds: number, now: number): string {
  const minutes = Math.floor(Math.max(0, now / 1000 - takenAtSeconds) / 60)
  if (minutes < 1) return t('work.room.preview.sleep.justNow')
  if (minutes < 60) return t('work.room.preview.sleep.minutesAgo', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('work.room.preview.sleep.hoursAgo', { n: hours })
  return t('work.room.preview.sleep.daysAgo', { n: Math.floor(hours / 24) })
}

export interface WakeProgress {
  machine: boolean
  tunnel: boolean
  agent: boolean
  port: boolean
}

/** 四步各自怎样：做完的打勾，第一步没做完的转圈，其余空着。 */
export function wakeSteps(progress: WakeProgress): AppWakeStep[] {
  const order: Array<[AppWakeStep['key'], string]> = [
    ['machine', 'work.room.preview.sleep.stepMachine'],
    ['tunnel', 'work.room.preview.sleep.stepTunnel'],
    ['agent', 'work.room.preview.sleep.stepAgent'],
    ['port', 'work.room.preview.sleep.stepPort'],
  ]
  let current = false
  return order.map(([key, label]) => {
    if (progress[key]) return { key, label, state: 'done' }
    if (current) return { key, label, state: 'todo' }
    current = true
    return { key, label, state: 'now' }
  })
}

export function usePanelPreviewAppSleep(deps: {
  props: PanelPreviewProps
  state: PreviewState
  displayed: () => PreviewFrame | null
  reload: () => Promise<void>
}) {
  const { props, state } = deps
  const snapshot = ref<{ html: string; takenAt: number } | null>(null)
  // 快照属于哪一件：换了产物，旧的那份就不归它了。
  let snapshotOf: string | null = null
  let fetchedFor: string | null = null
  const now = ref(Date.now())
  const ticker = setInterval(() => {
    now.value = Date.now()
  }, 30_000)

  const progress = ref<WakeProgress | null>(null)
  const wakeError = ref('')
  let poll: ReturnType<typeof setTimeout> | null = null
  let wakeStarted = 0
  let sawWorking = false

  /** 帧里的桥拍了一张：存到平台上，也立刻记在这里（睡着时不用再取一次）。存不上不打扰
   *  读者——下一次加载成功还会再拍。 */
  function keep(frame: PreviewFrame, shot: FrameSnapshot) {
    const topicId = props.topicId
    const artifactId = state.previewArtifactId.value
    if (!topicId || !artifactId || !frame.live || props.path) return
    snapshot.value = { html: shot.html, takenAt: Date.now() / 1000 }
    snapshotOf = artifactId
    void putPreviewSnapshot(topicId, artifactId, { html: shot.html, href: shot.href, title: shot.title }).catch(
      () => {}
    )
  }

  const asleep = computed(
    () => !!state.previewAppNote.value && !props.path && (!state.previewTunnelUp.value || !state.previewAppUp.value)
  )

  // 睡着了、手上又没有这一件的快照：去平台上取一次（换了产物再取）。
  watch(
    () => [asleep.value, state.previewArtifactId.value] as const,
    async ([sleeping, artifactId]) => {
      if (artifactId !== snapshotOf) snapshot.value = null
      const topicId = props.topicId
      if (!sleeping || !topicId || !artifactId || snapshot.value || fetchedFor === artifactId) return
      fetchedFor = artifactId
      try {
        const kept = await getPreviewSnapshot(topicId)
        if (kept && state.previewArtifactId.value === artifactId && !snapshot.value) {
          snapshot.value = { html: kept.html, takenAt: kept.taken_at }
          snapshotOf = artifactId
        }
      } catch {
        // 取不到就没有快照可顶：卡上照样说清楚，按钮照样能按。这一件没取成的不算「取
        // 过了」——否则一次网络抖动之后，这一次挂载里再睡着都不会重试，卡上永远空着。
        fetchedFor = null
      }
    },
    { immediate: true }
  )

  const appSnapshot = computed(() =>
    snapshot.value ? { html: snapshot.value.html, age: snapshotAge(snapshot.value.takenAt, now.value) } : null
  )

  function stopPolling() {
    if (poll) clearTimeout(poll)
    poll = null
  }

  function finish(error = '') {
    stopPolling()
    progress.value = null
    wakeError.value = error
  }

  async function check() {
    const topicId = props.topicId
    if (!topicId || !progress.value) return
    try {
      const status = await getPreviewWakeStatus(topicId)
      if (!progress.value) return
      sawWorking ||= status.agent_working
      progress.value = {
        machine: status.machine === 'ready' || status.tunnel_up,
        tunnel: status.tunnel_up,
        agent: status.app_up || (sawWorking && !status.agent_working && status.tunnel_up),
        port: status.app_up,
      }
      if (status.app_up) {
        // 应用答了：四步走完，重新载入预览。载入完就收进度——它抛错、或者载回来应用
        // 仍然睡着，都退回普通休眠态（按钮还能按）；不收的话卡会永远停在「四步全绿」
        // 上，而「唤醒」被 progress 判据挡着，只能重挂面板。
        progress.value = { machine: true, tunnel: true, agent: true, port: true }
        try {
          await deps.reload()
        } catch {
          // 载入失败已经由取数那一层报在面板上；这里只保证卡片让开。
        }
        finish()
        return
      }
    } catch {
      // 一次没问到不算失败，下一轮再问。
    }
    if (Date.now() - wakeStarted >= WAKE_BUDGET_MS) {
      finish(t('work.room.preview.sleep.wakeTimeout', { seconds: WAKE_BUDGET_MS / 1000, agent: props.agentName ?? '' }))
      return
    }
    poll = setTimeout(() => void check(), WAKE_POLL_MS)
  }

  async function wake() {
    const topicId = props.topicId
    if (!topicId || progress.value) return
    wakeError.value = ''
    wakeStarted = Date.now()
    sawWorking = false
    const tunnel = state.previewTunnelUp.value
    progress.value = { machine: tunnel, tunnel, agent: false, port: false }
    try {
      const answer = await wakePreviewApp(topicId)
      if (answer.reason === 'up') {
        progress.value = { machine: true, tunnel: true, agent: true, port: true }
        try {
          await deps.reload()
        } catch {
          // 同上：载入失败由面板自己报，这里只保证卡片让开。
        }
        finish()
        return
      }
    } catch (e) {
      finish(t('work.room.preview.sleep.wakeFailed', { error: e instanceof Error ? e.message : String(e) }))
      return
    }
    // 起了一轮（或者刚有人按过、队友正忙着）：都等它，一步步问。
    poll = setTimeout(() => void check(), WAKE_POLL_MS)
  }

  // 应用醒了、新页面也装上了：进度走完，卡让开。
  watch(
    () => [asleep.value, deps.displayed()?.id] as const,
    ([sleeping]) => {
      if (progress.value?.port && !sleeping) finish()
    }
  )
  // 换了房间或文件：上一处的唤醒和快照都不归这一格了。
  watch(
    () => [props.topicId, props.path],
    () => {
      finish()
      snapshot.value = null
      snapshotOf = null
      fetchedFor = null
    }
  )

  onScopeDispose(() => {
    stopPolling()
    clearInterval(ticker)
  })

  return {
    keep,
    appSnapshot,
    wakeSteps: computed(() => (progress.value ? wakeSteps(progress.value) : null)),
    wakeError,
    wake,
  }
}
