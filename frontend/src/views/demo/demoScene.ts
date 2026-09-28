// 文档里的动态演示：一份剧本（scenes/*.json）按步、按毫秒放出事件，这里把
// 「放到第几步的第几毫秒」算成一帧画面。画面由产品里真的组件画（房间消息行、
// 现场），所以这里只产出它们吃的数据：Block 和几张小表。
//
// 状态永远从头重放算出来，不在播放器里累积：往回跳一步、从文档那边直接跳到
// 第五步，得到的都是同一帧，不会因为跳的路径不同而长得不一样。
import type { Block } from '@/cx_types'

export type Focus = 'machine' | 'seats' | 'chat' | 'site' | 'tabs' | 'title' | 'backstage'

// 右下角「幕后」那一格画什么：界面上看不见、但这一段要讲的机制。
export type Backstage = 'memory' | 'pipeline' | 'devices'

export interface Person {
  name: string
  agent?: boolean
}

// 剧本里的一件事。`at` 是这一步开始后的毫秒数。
export type SceneEvent =
  // 有人在房间里说一句话（人或队友）。点名写成 <@handle>，和真消息一样。
  | { at: number; do: 'say'; who: string; text: string }
  // 对话栏里一条安静的分隔说明（不是谁说的话）。
  | { at: number; do: 'mark'; text: string }
  // 一位队友开始一轮。
  | { at: number; do: 'turn'; who: string; turn: string }
  // 现场里的一步工具调用。
  | {
      at: number
      do: 'act'
      who: string
      turn: string
      tool: string
      arg?: string
      platform?: boolean
      failed?: boolean
      error?: string
    }
  // 现场里队友自己说的一句（不是工具调用）。
  | { at: number; do: 'note'; who: string; turn: string; text: string }
  // 一轮结束。
  | { at: number; do: 'end'; turn: string }
  // 顶栏的机器说明换一句。
  | { at: number; do: 'machine'; text: string }
  // 一位队友的座位卡上换几项（工作目录、会话、此刻在干什么）。
  | { at: number; do: 'seat'; who: string; dir?: string; session?: string; state?: string }
  // 房间的重资源锁：谁占着，null 是放开。
  | { at: number; do: 'lock'; who: string | null }
  // 话题标题换了。
  | { at: number; do: 'title'; text: string }
  // 记忆文件树：建一个文件或者改它。`add` 在末尾加一行，`remove` 删掉等于它的那行，
  // `lines` 整份换掉；`gone` 把文件删掉。
  | { at: number; do: 'file'; path: string; add?: string; remove?: string; lines?: string[]; gone?: boolean }
  // 哪几份文件此刻正被装进上下文（空数组 = 没有）。
  | { at: number; do: 'inject'; paths: string[] }
  // 一次请求走到哪一站、那一站怎么说。`clear` 把所有站熄掉，好走下一条路线。
  | { at: number; do: 'hop'; station: string; state: StationState; note?: string }
  | { at: number; do: 'clear' }
  // 幕后那一格里的一个读数（剩余额度、心跳间隔……）。
  | { at: number; do: 'meter'; label: string; value: string }
  // 一台设备的状态。
  | { at: number; do: 'device'; id: string; status: DeviceStatus; text?: string }
  // 连接器终端里多一行。
  | { at: number; do: 'term'; text: string }

export type StationState = 'on' | 'ok' | 'deny' | 'off'
export type DeviceStatus = 'offline' | 'pairing' | 'online' | 'busy' | 'lost' | 'cooling'

export interface Station {
  id: string
  label: string
  // 这一站是什么，一句话。
  note?: string
}

export interface DeviceDef {
  id: string
  name: string
  kind: string
}

export interface SceneStep {
  label: string
  // 这一步画面上该看哪一块：那一块描一圈边，旁边挂 tag 这句短话。
  focus?: Focus
  tag?: string
  events: SceneEvent[]
}

export interface Scene {
  title: string
  project: string
  topic: string
  machine: string
  people: Record<string, Person>
  // 座位卡按这个顺序排；不写就不显示座位栏。
  seats?: string[]
  backstage?: Backstage
  // backstage = pipeline：从上到下的几站。
  stations?: Station[]
  // backstage = devices：有哪几台设备。
  devices?: DeviceDef[]
  // backstage = memory：开场时已经在的文件。
  files?: { path: string; lines: string[] }[]
  steps: SceneStep[]
}

export interface MemoryFile {
  path: string
  lines: string[]
  // 这一步里刚被改过。
  fresh: boolean
}

export interface Seat {
  who: string
  dir: string
  session: string
  state: string
}

export interface ChatLine {
  kind: 'message' | 'mark'
  id: string
  author: string
  text: string
  // 显示用的 HH:mm。
  time: string
}

export interface Frame {
  step: number
  topic: string
  machine: string
  lock: string | null
  seats: Seat[]
  chat: ChatLine[]
  site: Block[]
  // 在跑的轮次 id → 开始时刻（剧本时间换成的毫秒时间戳），给现场的状态条。
  running: Record<string, number>
  // 在跑的轮次各是谁的，给工作标签报名字。
  runningWho: string[]
  focus: Focus | null
  tag: string
  files: MemoryFile[]
  injected: string[]
  stations: Record<string, { state: StationState; note: string }>
  // 请求此刻停在哪一站（最近一次亮起的那一站）。
  at: string | null
  meters: { label: string; value: string }[]
  devices: Record<string, { status: DeviceStatus; text: string }>
  term: string[]
}

// 一步放完之后停多久再算「这一步放完了」：最后一件事出来，读的人要有时间看见它。
export const STEP_TAIL_MS = 1600

// 剧本时间换成画面上的钟点：从一个固定时刻起，剧本里的 1 毫秒当 8 毫秒过。
// 这样现场组头的「几步 · 多久」读起来像真的一轮，而不是 0 秒。
const CLOCK_BASE = Date.parse('2026-09-28T14:02:00+08:00')
const CLOCK_SCALE = 8

export function stepDuration(step: SceneStep): number {
  return step.events.reduce((m, e) => Math.max(m, e.at), 0) + STEP_TAIL_MS
}

function hhmm(ms: number): string {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 放到第 `step` 步（从 0 数）的第 `elapsed` 毫秒时，画面是什么样。 */
export function frameAt(scene: Scene, step: number, elapsed: number): Frame {
  const last = Math.max(0, Math.min(step, scene.steps.length - 1))
  let topic = scene.topic
  let machine = scene.machine
  let lock: string | null = null
  const seats = new Map<string, Seat>((scene.seats ?? []).map((who) => [who, { who, dir: '', session: '', state: '' }]))
  const chat: ChatLine[] = []
  const site: Block[] = []
  const running = new Map<string, { at: number; who: string }>()
  const files = new Map<string, MemoryFile>(
    (scene.files ?? []).map((f) => [f.path, { path: f.path, lines: [...f.lines], fresh: false }])
  )
  let injected: string[] = []
  const stations: Frame['stations'] = {}
  let at: string | null = null
  const meters = new Map<string, string>()
  const devices: Frame['devices'] = {}
  const term: string[] = []

  let offset = 0
  for (let i = 0; i <= last; i++) {
    const s = scene.steps[i]
    const until = i < last ? Infinity : elapsed
    for (const f of files.values()) f.fresh = false
    s.events.forEach((e, n) => {
      if (e.at > until) return
      const clock = CLOCK_BASE + (offset + e.at) * CLOCK_SCALE
      const created_at = new Date(clock).toISOString()
      const id = `s${i}e${n}`
      switch (e.do) {
        case 'say':
          chat.push({ kind: 'message', id, author: e.who, text: e.text, time: hhmm(clock) })
          break
        case 'mark':
          chat.push({ kind: 'mark', id, author: '', text: e.text, time: hhmm(clock) })
          break
        case 'turn':
          running.set(e.turn, { at: clock, who: e.who })
          break
        case 'end':
          running.delete(e.turn)
          break
        case 'act':
          site.push({
            id,
            topic_id: 'demo',
            kind: 'event',
            author_type: 'participant',
            author: e.who,
            content: e.arg ? `${e.tool}\n${e.arg}` : e.tool,
            turn_id: e.turn,
            meta: {
              tool: e.tool,
              arg: e.arg ?? '',
              ...(e.platform ? { platform: true } : {}),
              ...(e.failed ? { failed: true, error: e.error ?? '' } : {}),
            },
            created_at,
          })
          break
        case 'note':
          site.push({
            id,
            topic_id: 'demo',
            kind: 'event',
            author_type: 'participant',
            author: e.who,
            content: e.text,
            turn_id: e.turn,
            meta: { progress: true },
            created_at,
          })
          break
        case 'machine':
          machine = e.text
          break
        case 'seat': {
          const seat = seats.get(e.who) ?? { who: e.who, dir: '', session: '', state: '' }
          if (e.dir !== undefined) seat.dir = e.dir
          if (e.session !== undefined) seat.session = e.session
          if (e.state !== undefined) seat.state = e.state
          seats.set(e.who, seat)
          break
        }
        case 'lock':
          lock = e.who
          break
        case 'title':
          topic = e.text
          break
        case 'file': {
          if (e.gone) {
            files.delete(e.path)
            break
          }
          const f = files.get(e.path) ?? { path: e.path, lines: [], fresh: false }
          if (e.lines) f.lines = [...e.lines]
          if (e.remove !== undefined) f.lines = f.lines.filter((l) => l !== e.remove)
          if (e.add !== undefined) f.lines = [...f.lines, e.add]
          f.fresh = true
          files.set(e.path, f)
          break
        }
        case 'inject':
          injected = [...e.paths]
          break
        case 'hop':
          stations[e.station] = { state: e.state, note: e.note ?? '' }
          if (e.state !== 'off') at = e.station
          break
        case 'clear':
          for (const k of Object.keys(stations)) delete stations[k]
          at = null
          break
        case 'meter':
          meters.set(e.label, e.value)
          break
        case 'device':
          devices[e.id] = { status: e.status, text: e.text ?? '' }
          break
        case 'term':
          term.push(e.text)
          break
      }
    })
    offset += stepDuration(s)
  }

  const current = scene.steps[last]
  const runningWho: string[] = []
  for (const { who } of running.values()) if (!runningWho.includes(who)) runningWho.push(who)
  return {
    step: last,
    topic,
    machine,
    lock,
    seats: [...seats.values()],
    chat,
    site,
    running: Object.fromEntries([...running].map(([turn, r]) => [turn, r.at])),
    runningWho,
    focus: current?.focus ?? null,
    tag: current?.tag ?? '',
    files: [...files.values()].sort((a, b) => a.path.localeCompare(b.path)),
    injected,
    stations,
    at,
    meters: [...meters].map(([label, value]) => ({ label, value })),
    devices,
    term,
  }
}

/** 剧本自己有没有写错：会让画面悄悄不对的那几类，在测试里就说出来。 */
export function checkScene(scene: Scene): string[] {
  const problems: string[] = []
  const open = new Set<string>()
  scene.steps.forEach((s, i) => {
    const where = `step ${i + 1}「${s.label}」`
    let prev = -1
    for (const e of s.events) {
      if (e.at < prev) problems.push(`${where}: events are out of order at ${e.at}ms`)
      prev = e.at
      if ('who' in e && e.who !== null && !(e.who in scene.people)) problems.push(`${where}: nobody called «${e.who}»`)
      if (e.do === 'turn') open.add(e.turn)
      if ((e.do === 'act' || e.do === 'note') && !open.has(e.turn))
        problems.push(`${where}: «${e.turn}» is not running`)
      if (e.do === 'hop' && !(scene.stations ?? []).some((st) => st.id === e.station)) {
        problems.push(`${where}: no station «${e.station}»`)
      }
      if (e.do === 'device' && !(scene.devices ?? []).some((d) => d.id === e.id))
        problems.push(`${where}: no device «${e.id}»`)
      if (e.do === 'end') {
        if (!open.has(e.turn)) problems.push(`${where}: ends «${e.turn}», which is not running`)
        open.delete(e.turn)
      }
    }
  })
  return problems
}
