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
import { appAsleepFrom } from '../../lib/previewChain'
import { connectionInfo, prefersLightVideo } from '../../lib/videoPlayback'
import { frameArtifactId } from '../usePreviewFrames'

/** 唤醒进度里的一步。 */
export interface AppWakeStep {
  key: 'machine' | 'tunnel' | 'agent' | 'port'
  label: string
  state: 'done' | 'now' | 'todo'
}

/** 等多久还没起来就认输、把按钮还回去：机器冷启动实测要两分多钟，再给一些余量。 */
export const WAKE_BUDGET_MS = 180_000

/** 问到哪一步了，隔多久再问一次。
 *
 *  头几秒问得密（机器开机、端口应答都可能一两秒就过去），之后一格一格松开：慢网和机器
 *  排队的时候，两秒一问会在同一段时间里堆出几十个请求，而且每一个都要等前面那个回来才
 *  排下一个。松到八秒仍然漏不了状态——四步里每一步都还会被问到，只是不再每两秒一次。 */
export const WAKE_POLL_STEPS_MS = [2_000, 2_000, 3_000, 5_000, 8_000] as const

/** 第 n 次轮询之后隔多久（从 0 数起，最后那一档一直用下去）。 */
export function wakePollDelay(round: number): number {
  const last = WAKE_POLL_STEPS_MS.length - 1
  return WAKE_POLL_STEPS_MS[Math.min(Math.max(round, 0), last)]
}

/** 快照是多久以前留的，说成一句话。 */
export function snapshotAge(takenAtSeconds: number, now: number): string {
  const minutes = Math.floor(Math.max(0, now / 1000 - takenAtSeconds) / 60)
  if (minutes < 1) return t('work.room.preview.sleep.justNow')
  if (minutes < 60) return t('work.room.preview.sleep.minutesAgo', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('work.room.preview.sleep.hoursAgo', { n: hours })
  return t('work.room.preview.sleep.daysAgo', { n: Math.floor(hours / 24) })
}

/** 快照是哪一页：有标题就说标题，否则说路径。
 *
 *  一份应用只留一张快照，几个读者同时开着时**后写的盖掉先写的**（平台按应用存一份，
 *  不按读者存）。所以卡上得说清停在哪一页，否则另一个读者会以为自己那一页被冻住了。
 *  都没有就答空串，卡上那句不带这一截。 */
export function snapshotWhere(href: string, title: string): string {
  const name = (title || '').trim()
  if (name) return name
  if (!href) return ''
  try {
    const url = new URL(href)
    return url.pathname + url.search
  } catch {
    return href
  }
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
  /** 这台设备是不是在省流（`lib/videoPlayback` 那一套判据：开了省流，或网差到 2G）。
   *  开着就不主动去取那份快照——一份最大 2 MB，而它只是「睡着时顶在底下给你看看」，
   *  省流的人多半宁可看一句「没有快照」。真需要时按唤醒。 */
  const light = prefersLightVideo(connectionInfo())
  // 快照里还记着是哪一页（`href` / `title`）：一份应用只有一张快照，几个读者同时开着时
  // 后写的盖掉先写的——不说清是哪一页，读者会以为睡着前停在的就是自己那一页。
  const snapshot = ref<{ html: string; takenAt: number; href: string; title: string } | null>(null)
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
  /** 这是第几次问（第 n 次之后隔多久见 `wakePollDelay`）。 */
  let wakeRound = 0
  let sawWorking = false
  /** 四步走完、卡收掉之后，还在重新载入预览的这一段。 */
  let settling = false

  /** 帧里的桥拍了一张：存到平台上，也立刻记在这里（睡着时不用再取一次）。存不上不打扰
   *  读者——下一次加载成功还会再拍。
   *
   *  桥上不来（`identity` 里读不出件）的帧照收：那是还没走到授权、或者测试里搭的裸帧，
   *  认不出是哪一件也不能就把它当别人的。 */
  function keep(frame: PreviewFrame, shot: FrameSnapshot) {
    const topicId = props.topicId
    const artifactId = state.previewArtifactId.value
    if (!topicId || !artifactId || !frame.live || props.path) return
    // 这张快照得确实是这一件拍的：换件时指针先动、新文档还在路上，旧帧仍会报一张上来，
    // 照收就会署成新那一件的快照——那一件睡着时显示的会是上一件的页面。
    const shotFor = frameArtifactId(frame)
    if (shotFor && shotFor !== artifactId) return
    snapshot.value = { html: shot.html, takenAt: Date.now() / 1000, href: shot.href, title: shot.title }
    snapshotOf = artifactId
    void putPreviewSnapshot(topicId, artifactId, { html: shot.html, href: shot.href, title: shot.title }).catch(
      () => {}
    )
  }

  // 「睡着了」的判据和画那一半是同一个（`lib/previewChain` 的 `appAsleepFrom`）：这里
  // 自己再写一份的话，平台读不到预览信息时这一半仍会说睡着、白白去平台上取一次快照——
  // 那一次既没人看得到，也拿不到东西。
  const asleep = computed(() =>
    props.path || !state.previewAppNote.value
      ? null
      : appAsleepFrom({
          platformOk: !state.previewError.value,
          live: true,
          tunnelUp: state.previewTunnelUp.value,
          appAnswering: state.previewAppUp.value,
        })
  )

  // 睡着了、手上又没有这一件的快照：去平台上取一次（换了产物再取）。
  watch(
    () => [asleep.value, state.previewArtifactId.value] as const,
    async ([sleeping, artifactId]) => {
      if (artifactId !== snapshotOf) snapshot.value = null
      const topicId = props.topicId
      if (!sleeping || !topicId || !artifactId || snapshot.value || fetchedFor === artifactId) return
      // 省流：不替人下这份快照。卡上照样说「这个应用还没有留下快照」并给唤醒按钮。
      if (light) return
      fetchedFor = artifactId
      try {
        const kept = await getPreviewSnapshot(topicId)
        if (kept && state.previewArtifactId.value === artifactId && !snapshot.value) {
          snapshot.value = { html: kept.html, takenAt: kept.taken_at, href: kept.href, title: kept.title }
          snapshotOf = artifactId
        }
      } catch {
        // 取不到就没有快照可顶：卡上照样说清楚，按钮照样能按。这一件没取成的不算「取
        // 过了」——否则一次网络抖动之后，这一次挂载里再睡着都不会重试，卡上永远空着。
        // 只抹自己这一件的记号：请求在路上时换了产物，标记已经是新产物的了，抹掉会让
        // 它下一轮又多发一次。
        if (state.previewArtifactId.value === artifactId) fetchedFor = null
      }
    },
    { immediate: true }
  )

  const appSnapshot = computed(() =>
    snapshot.value
      ? {
          html: snapshot.value.html,
          age: snapshotAge(snapshot.value.takenAt, now.value),
          where: snapshotWhere(snapshot.value.href, snapshot.value.title),
        }
      : null
  )

  function stopPolling() {
    if (poll) clearTimeout(poll)
    poll = null
  }

  function finish(error = '') {
    stopPolling()
    progress.value = null
    settling = false
    wakeError.value = error
  }

  /** 应用答了：四步全绿，重新载入预览，再把卡片收掉。
   *
   *  载入抛错、或者载回来应用仍然睡着，都退回普通休眠态（按钮还能按）；不收的话卡会
   *  永远停在「四步全绿」上，而「唤醒」被 progress 判据挡着，只能重挂面板。载入失败
   *  由取数那一层报在面板上，这里只保证卡片让开。 */
  async function settle() {
    // 四步打满之后立刻收进度：再留着，舞台上那句「运行环境已休眠」会继续盖在一个已经
    // 答上、正在重新载入的应用上；收掉之后由 `asleep` 决定要不要再出卡——载入失败或载
    // 回来还是睡着，都会退回普通休眠态（按钮能再按）。
    //
    // 但重新载入还没走完之前不能算「没人正在干」：进度一收，卡上就换回那颗「唤醒」按钮，
    // 谁在这时再按一下就会再起一轮唤醒、再排一次载入，两次导航互相顶掉。`settling` 就是
    // 这段空档的占位，它和 progress 一起挡重入。
    progress.value = null
    settling = true
    try {
      await deps.reload()
    } catch {
      // 见上：报错在别处，卡片要收。
    }
    finish()
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
        await settle()
        return
      }
    } catch {
      // 一次没问到不算失败，下一轮再问。
    }
    if (Date.now() - wakeStarted >= WAKE_BUDGET_MS) {
      finish(t('work.room.preview.sleep.wakeTimeout', { seconds: WAKE_BUDGET_MS / 1000, agent: props.agentName ?? '' }))
      return
    }
    wakeRound += 1
    poll = setTimeout(() => void check(), wakePollDelay(wakeRound))
  }

  async function wake() {
    const topicId = props.topicId
    if (!topicId || progress.value || settling) return
    wakeError.value = ''
    wakeStarted = Date.now()
    wakeRound = 0
    sawWorking = false
    const tunnel = state.previewTunnelUp.value
    progress.value = { machine: tunnel, tunnel, agent: false, port: false }
    try {
      const answer = await wakePreviewApp(topicId)
      if (answer.reason === 'up') {
        await settle()
        return
      }
    } catch (e) {
      finish(t('work.room.preview.sleep.wakeFailed', { error: e instanceof Error ? e.message : String(e) }))
      return
    }
    // 起了一轮（或者刚有人按过、队友正忙着）：都等它，一步步问。
    wakeRound = 1
    poll = setTimeout(() => void check(), wakePollDelay(wakeRound))
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
