// 项目本体（root 话题）里那张「开始清单」：一个刚建好的项目要走过哪几步，才算
// 真的开起来了。
//
// 每一步「做没做」都从别处的真实状态推出来，不落字段、不加迁移，也不记「看过
// 没有」：这个人在项目里跟芝士说上过话没有（这一栏看得见的加上服务端答的）、这
// 个人往项目里放过材料没有、代码仓库里合进过一次被采纳的改动没有（服务端答）、
// 名册上有没有第二个人。两件必做的都做完，这张卡自己就没有存在的理由了。
//
// 「让成果进代码仓库」不看仓库接没接上：平台托管的项目一建好就有仓库，往资料库里
// 放个文件也会把它备好，那时候里面什么成果都还没有，打勾就是说假话。
//
// 唯一落盘的是右上角那一下「不再提示」——那是一个人的选择，不是项目的数据，所以
// 放在他自己这台机器上（localStorage），不占数据库。
import type { ProjectMemberRow } from '../cx_types'

import { computed, ref, watch } from 'vue'

import { getGettingStarted } from '../api'
import { listProjectLibrary } from '../lib/libraryApi'
import { myHandle } from '../me'

export type GettingStartedStepKey = 'talk' | 'materials' | 'repo' | 'people'

export interface GettingStartedStep {
  key: GettingStartedStepKey
  done: boolean
}

export interface GettingStartedOptions {
  projectId: () => string | null
  /** 只有项目本体上才有这份清单，别的话题不画。 */
  on: () => boolean
  /**
   * 这一栏自己看得见的：芝士在这里开过口。看不见的那部分（任务对话里的来回）由
   * 服务端答，见 `talkedElsewhere`。
   */
  agentHasSpoken: () => boolean
  /** 这个人往这个房间里放过附件。 */
  roomHasAttachment: () => boolean
  /** 项目名册（workspace store 里那一份，不再单独请求）。 */
  members: () => ProjectMemberRow[]
  /**
   * 除了这张卡，还有谁在读这几个事实（项目本体上那层手把手引导）。
   *
   * 卡做完两件必做的就退场，引导还要往下走到「进仓库」「请同事」——「进仓库」的
   * 事实是服务端那条网络判据给的，卡一退场就问不到了，「不知道」会被当成「还没
   * 做」，气泡于是指着已经做完的那一步。所以卡之外还有人要读时，网络判据继续问。
   */
  alsoProbe?: () => boolean
}

function dismissKey(projectId: string): string {
  return `cheese.gettingStarted.dismissed.${projectId}`
}

function readDismissed(projectId: string | null): boolean {
  if (!projectId) return false
  try {
    return localStorage.getItem(dismissKey(projectId)) === '1'
  } catch {
    // 读不到（无痕模式之类）就当没关过：多提示一次，好过永远不再提示。
    return false
  }
}

export function useGettingStarted(opts: GettingStartedOptions) {
  const projectId = computed(opts.projectId)
  const dismissed = ref(false)
  const libraryCount = ref<number | null>(null)
  /** 服务端说他在项目里别的对话里跟芝士说上过话没有；还没问到是 null。 */
  const talkedElsewhere = ref<boolean | null>(null)
  /** 服务端说项目的仓库里合进过一次被采纳的改动没有；还没问到是 null。 */
  const landed = ref<boolean | null>(null)

  // 换项目（或第一次拿到 project_id）就重新读一遍：上一条关掉的记录、上一个项目
  // 取到的资料库和仓库状态都不能跟着过来。
  watch(
    projectId,
    (pid) => {
      dismissed.value = readDismissed(pid)
      libraryCount.value = null
      talkedElsewhere.value = null
      landed.value = null
    },
    { immediate: true }
  )

  const talkDone = computed(() => opts.agentHasSpoken() || talkedElsewhere.value === true)
  // 材料两条路都算数：直接拖进这个房间，或者放进项目资料库。房间里那份本地就知
  // 道，所以只在「房间里还没有附件」时才去问资料库——大部分人是在房间里给的。
  const materialsDone = computed(() => opts.roomHasAttachment() || (libraryCount.value ?? 0) > 0)
  const repoDone = computed(() => landed.value === true)
  const peopleDone = computed(() => {
    const me = myHandle()
    return opts.members().some((m) => !m.agent && m.user_handle !== me)
  })

  const steps = computed<GettingStartedStep[]>(() => [
    { key: 'talk', done: talkDone.value },
    { key: 'materials', done: materialsDone.value },
    { key: 'repo', done: repoDone.value },
    { key: 'people', done: peopleDone.value },
  ])

  const visible = computed(
    () => opts.on() && !!projectId.value && !dismissed.value && !(talkDone.value && materialsDone.value)
  )

  // 两张网络判据各问一次，问完就停：清单画在项目本体上，而项目本体是每次进来都
  // 会开的那一页，不该每次开都多发两个请求。服务端那张一次答「说过话没有」和「进
  // 过仓库没有」两条，两条都打了勾就不再问。
  const probesOn = computed(() => visible.value || (opts.alsoProbe?.() ?? false))
  const needsLibrary = computed(() => probesOn.value && !opts.roomHasAttachment() && libraryCount.value === null)
  const needsServer = computed(() => probesOn.value && !(talkDone.value && repoDone.value))

  async function loadLibrary() {
    const pid = projectId.value
    if (!pid) return
    try {
      const page = await listProjectLibrary(pid, { flat: true, limit: 1 })
      if (projectId.value === pid) libraryCount.value = page.data.length
    } catch {
      // 问不到就照「还没有」算：这一步显示成没做，人去资料库看一眼也不吃亏，
      // 比整张卡消失强。
      if (projectId.value === pid) libraryCount.value = 0
    }
  }

  async function loadServer() {
    const pid = projectId.value
    if (!pid) return
    try {
      const state = await getGettingStarted(pid)
      if (projectId.value !== pid) return
      talkedElsewhere.value = state.talked
      landed.value = state.landed
    } catch {
      // 问不到就照这一栏自己看到的算；仓库那一步照「还没做」算——说少了只是多提示一次。
      if (projectId.value !== pid) return
      talkedElsewhere.value = false
      landed.value = false
    }
  }

  watch(
    needsLibrary,
    (on) => {
      if (on) void loadLibrary()
    },
    { immediate: true }
  )
  // 这两条都在别处发生：人去任务里跟芝士聊完、或者去采纳了一次交付，再回到这一栏，
  // 得重新问一次。所以除了第一次要读，每次回到这一栏（`on` 由假变真）时，还没做完
  // 就再问。
  watch(
    [needsServer, () => opts.on()] as const,
    ([need, on], [wasNeed, wasOn]) => {
      if (need && (!wasNeed || (on && !wasOn))) void loadServer()
    },
    { immediate: true }
  )

  function dismiss() {
    dismissed.value = true
    const pid = projectId.value
    if (!pid) return
    try {
      localStorage.setItem(dismissKey(pid), '1')
    } catch {
      // 存不进去：这一次先藏起来，下次进来还会出现。
    }
  }

  return { visible, steps, dismiss }
}
