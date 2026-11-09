// Everything the demo screen shows is read from this one reactive object, and
// every click (a person's or the scripted cursor's) goes through the actions
// below, so playing, stepping and clicking by hand always agree.
import { reactive } from 'vue'

import { ASK, MINUTES_DOC } from './data'

export type Status = 'talk' | 'run' | 'wait' | 'done'
export type Center = 'inbox' | 'chan' | 'files' | 'settings'

export interface Thing {
  id: string
  title: string
  chan: string
  owner: string
  status: Status
  replies: number
  last: string
  at: string
}

export interface ChatLine {
  id: string
  who: string
  time: string
  text: string
  thing?: string
}

export interface HistoryEntry {
  who: string
  what: string
  when: string
  fresh?: boolean
}

export const PHASES = ['讨论', '在做', '等你采纳', '完成'] as const
export const PHASE: Record<Status, number> = { talk: 0, run: 1, wait: 2, done: 3 }

function initial() {
  return {
    center: 'chan' as Center,
    chan: 'c-exp',
    thing: null as string | null,
    rtab: 'chat',
    unread: { 'c-papers': 2 } as Record<string, number>,
    sheet: false,
    morph: false,
    typing: '',
    live: 0,
    term: 0,
    file: MINUTES_DOC,
    ftab: 'pending',
    suggAccepted: false,
    typed: '',
    toast: '',
    toastTick: 0,
    pendingFirst: true,
    settingsAgentDirect: false,
    settingsProtectCode: false,
    history: [
      { who: 'wanglei', what: '改了《组会纪要 11-07》', when: '10 分钟前' },
      { who: 'chensiyuan', what: '任务「补长描述截断实验」采纳：改了 2 个文件', when: '昨天' },
      { who: 'zhaoyutong', what: '上传了 coco_captions.xlsx', when: '11-06' },
    ] as HistoryEntry[],
    things: [
      {
        id: 't1',
        title: '统计 COCO 描述长度分布',
        chan: 'c-exp',
        owner: 'zhaoyutong',
        status: 'wait',
        replies: 4,
        last: '芝士：超过 77 个 token 的描述占 6.3%',
        at: '2026-11-09T02:05:00Z',
      },
      {
        id: 't2',
        title: '复现 BLIP 基线',
        chan: 'c-papers',
        owner: 'wanglei',
        status: 'run',
        replies: 6,
        last: '芝士：Flickr30K 跑到第 3/5 个 checkpoint',
        at: '2026-11-09T01:40:00Z',
      },
      {
        id: 't3',
        title: '整理组会汇报幻灯片',
        chan: 'c-meeting',
        owner: 'linxiao',
        status: 'done',
        replies: 3,
        last: '已采纳 · slides.pdf 在项目文件里',
        at: '2026-11-08T09:00:00Z',
      },
    ] as Thing[],
    chat: {
      'c-exp': [
        {
          id: 'm1',
          who: 'wanglei',
          time: '09:12',
          text: 'ViT-B/32 零样本检索跑完了，Flickr30K R@1 是 78.2，比论文的 79.3 低一点。',
        },
        {
          id: 'm2',
          who: 'chensiyuan',
          time: '09:20',
          text: '可能是图像预处理不一样，我们几个的评测脚本口径也不统一。',
        },
        { id: 'm3', who: 'zhaoyutong', time: '10:02', text: '<@cheese> 统计一下 COCO 描述的长度分布', thing: 't1' },
      ],
      'c-papers': [
        { id: 'p1', who: 'wanglei', time: '08:31', text: '这周读 BLIP，我先把基线跑起来。', thing: 't2' },
        { id: 'p2', who: 'chensiyuan', time: '10:15', text: 'BLIP 的 COCO 结果是在 5K 上报的，别搞混。' },
      ],
      'c-meeting': [
        { id: 'g1', who: 'linxiao', time: '昨天', text: '<@cheese> 把这周的进展整理成组会幻灯片', thing: 't3' },
      ],
      'c-general': [{ id: 'z1', who: 'wanglei', time: '08:40', text: '这个频道最近没有新的讨论。' }],
    } as Record<string, ChatLine[]>,
    showNotes: false,
  }
}

export const S = reactive(initial())

export function reset(): void {
  const keep = S.showNotes
  Object.assign(S, initial())
  S.showNotes = keep
}

export const thingById = (id: string | null) => S.things.find((t) => t.id === id) ?? null
export const t4 = () => thingById('t4')

export function toast(text: string): void {
  S.toast = text
  S.toastTick++
}

export function go(center: Center, extra: Partial<typeof S> = {}): void {
  Object.assign(S, { center }, extra)
  if (center !== 'chan') S.thing = null
  if (center === 'chan') delete S.unread[S.chan]
}

export function openThing(id: string): void {
  const t = thingById(id)
  if (!t) return
  go('chan', { chan: t.chan, thing: id, rtab: 'chat' })
}

export function send(): void {
  if (t4()) return
  S.things.unshift({
    id: 't4',
    title: '统一评测脚本，固定输出 R@1/R@5/R@10',
    chan: 'c-exp',
    owner: 'linxiao',
    status: 'talk',
    replies: 1,
    last: '芝士：两边预处理不一样，建议统一成一份',
    at: '2026-11-09T02:31:00Z',
  })
  S.chat['c-exp']!.push({ id: 'm4', who: 'linxiao', time: '10:31', text: ASK, thing: 't4' })
  S.typing = ''
  toast('在这条消息下开了一个讨论')
}

export function reply(n: number, last: string): void {
  const t = t4()
  if (!t) return
  t.replies = n
  t.last = last
}

export function kickoff(): void {
  if (t4()?.status === 'talk') S.sheet = true
}

export function confirmKick(): void {
  const t = t4()
  if (!t) return
  S.sheet = false
  t.status = 'run'
  t.last = '芝士：开始改了'
  S.morph = true
  S.rtab = 'site'
  S.live = 0
  S.term = 0
  toast('开工了，交给芝士')
  setTimeout(() => (S.morph = false), 1200)
}

export function finish(): void {
  const t = t4()
  if (!t) return
  t.status = 'wait'
  t.last = '芝士：Flickr30K R@1 = 79.1，等你采纳'
  S.live = 5
  S.term = 4
}

export function accept(): void {
  const t = t4()
  if (!t || t.status !== 'wait') return
  t.status = 'done'
  t.last = '已采纳 · 合进正式版'
  S.rtab = 'result'
  for (const h of S.history) h.fresh = false
  S.history.unshift({
    who: 'cheese',
    what: '任务「统一评测脚本」采纳：改了 2 个文件，新增 eval_results.md',
    when: '刚刚',
    fresh: true,
  })
  toast('已采纳：合进项目文件的正式版')
}

export function suggAccept(): void {
  if (S.suggAccepted) return
  S.suggAccepted = true
  for (const h of S.history) h.fresh = false
  S.history.unshift({ who: 'linxiao', what: '接受了芝士对《组会纪要》的建议', when: '刚刚', fresh: true })
  toast('建议已接受，进了正式版')
}

export function undo(index: number): void {
  const h = S.history[index]
  if (!h) return
  S.history.splice(index, 1)
  if (h.what.startsWith('接受了芝士对')) S.suggAccepted = false
  toast(`已撤回：${h.what}`)
}
