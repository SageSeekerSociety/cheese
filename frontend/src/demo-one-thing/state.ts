// Everything the demo screen shows is read from this one reactive object, and
// every click (a person's or the scripted cursor's) goes through the actions
// below, so playing, stepping and clicking by hand always agree.
import type { ReactionAgg } from '@/cx_types'

import { reactive } from 'vue'

import { ASK, dayAt, ME, minsAgo, MINUTES_DOC, NAMES } from './data'

export type Status = 'talk' | 'run' | 'wait' | 'done'
export type Center = 'inbox' | 'chan' | 'files' | 'settings'

/** One thing said: in a channel, or inside a 一件事. */
export interface ChatLine {
  id: string
  who: string
  at: string
  text: string
  thing?: string
  reactions?: ReactionAgg[]
  /** The message this one answers (引用回复). */
  replyTo?: string
}

/** A quiet line in a 一件事's conversation: 开工, 采纳. */
export interface MarkLine {
  id: string
  mark: string
  at: string
  ok?: boolean
}

export interface Thing {
  id: string
  title: string
  chan: string
  owner: string
  status: Status
  replies: number
  last: string
  at: string
  /** What was said inside it (the scripted 一件事 builds its own from the step it is at). */
  talk?: (ChatLine | MarkLine)[]
  result?: string
}

export interface HistoryEntry {
  who: string
  what: string
  at: string
  fresh?: boolean
}

export const PHASES = ['讨论', '在做', '等你采纳', '完成'] as const
export const PHASE: Record<Status, number> = { talk: 0, run: 1, wait: 2, done: 3 }

const rx = (emoji: string, ...authors: string[]): ReactionAgg => ({ emoji, count: authors.length, authors })

function initial() {
  return {
    center: 'chan' as Center,
    chan: 'c-exp',
    thing: null as string | null,
    rtab: 'chat',
    /** The channel's own right panel: open (概览) or folded away. */
    chanPanel: true,
    /** 专注：the open 一件事 takes the center column too. */
    focus: false,
    unread: { 'c-papers': 2, 'c-general': 1 } as Record<string, number>,
    sheet: false,
    morph: false,
    typing: '',
    thingTyping: '',
    replyTo: null as string | null,
    thingReplyTo: null as string | null,
    live: 0,
    file: MINUTES_DOC,
    ftab: 'pending',
    suggAccepted: false,
    suggRejected: false,
    typed: '',
    toast: '',
    toastTick: 0,
    settingsSection: 'project',
    settingsAgentDirect: false,
    settingsProtectCode: false,
    settingsNotify: 'mentions',
    settingsRuntime: 'a100',
    /** When the scripted ask went out (the 一件事's own clock counts from it). */
    askAt: '',
    /** What 林晓 said by hand inside each 一件事, after the script's lines. */
    extra: {} as Record<string, ChatLine[]>,
    history: [
      { who: 'wanglei', what: '改了《组会纪要 11-07》', at: minsAgo(9) },
      { who: 'chensiyuan', what: '采纳了任务「补长描述截断实验」：改了 2 个文件', at: dayAt(1, 17, 42) },
      { who: 'zhaoyutong', what: '上传了 report/中期报告.docx', at: dayAt(1, 11, 5) },
      { who: 'wanglei', what: '采纳了任务「BLIP 精读笔记」：新增 docs/papers/BLIP-精读.md', at: dayAt(3, 15, 20) },
    ] as HistoryEntry[],
    things: [
      {
        id: 't1',
        title: '统计 COCO 描述长度分布',
        chan: 'c-exp',
        owner: 'zhaoyutong',
        status: 'wait',
        replies: 4,
        last: '芝士：超过 77 个 token 的描述占 6.3%，图表在成果里',
        at: minsAgo(41),
        talk: [
          { id: 't1a', who: 'zhaoyutong', at: minsAgo(78), text: '<@cheese> 统计一下 COCO 描述的长度分布' },
          {
            id: 't1b',
            who: 'cheese',
            at: minsAgo(77),
            text: '好。用 CLIP 的 tokenizer 数 token，训练集和 5K 测试集分开统计，按 ≤20 / 21–77 / >77 三档出表。',
          },
          { id: 't1k', mark: '赵雨桐 开工 · 交给芝士', at: minsAgo(76) },
          {
            id: 't1c',
            who: 'cheese',
            at: minsAgo(41),
            text: '做完了，等你采纳：\n\n| token 数 | 训练集 | 5K 测试集 |\n| --- | --- | --- |\n| ≤ 20 | 71.4% | 72.0% |\n| 21–77 | 22.3% | 21.9% |\n| > 77 | **6.3%** | 6.1% |\n\n超过 77 的会被截断，长描述截断实验可以只看这 6% 左右。',
          },
        ],
        result:
          '| token 数 | 训练集 | 5K 测试集 |\n| --- | --- | --- |\n| ≤ 20 | 71.4% | 72.0% |\n| 21–77 | 22.3% | 21.9% |\n| > 77 | 6.3% | 6.1% |',
      },
      {
        id: 't2',
        title: '复现 BLIP 基线',
        chan: 'c-papers',
        owner: 'wanglei',
        status: 'run',
        replies: 6,
        last: '芝士：Flickr30K 跑到第 3/5 个 checkpoint',
        at: minsAgo(6),
        talk: [
          {
            id: 't2a',
            who: 'wanglei',
            at: dayAt(1, 10, 31),
            text: '<@cheese> 用官方 checkpoint 把 BLIP 的零样本检索跑一遍，先 Flickr30K。',
          },
          { id: 't2k', mark: '王磊 开工 · 交给芝士', at: dayAt(1, 10, 33) },
          {
            id: 't2b',
            who: 'cheese',
            at: dayAt(1, 10, 52),
            text: '环境装好了，`transformers==4.44`，五个 checkpoint 依次跑，每个大约 25 分钟。',
          },
          { id: 't2c', who: 'wanglei', at: dayAt(1, 11, 4), text: '好，跑完把每个 checkpoint 的 R@1 列出来' },
          {
            id: 't2d',
            who: 'cheese',
            at: minsAgo(6),
            text: 'Flickr30K 跑到第 3/5 个 checkpoint，目前最好的是 `blip_base_14M`，R@1 = 81.9。',
          },
        ],
      },
      {
        id: 't3',
        title: '整理组会汇报幻灯片',
        chan: 'c-meeting',
        owner: 'linxiao',
        status: 'done',
        replies: 3,
        last: '已采纳 · slides.pdf 在项目文件里',
        at: dayAt(1, 18, 12),
        talk: [
          { id: 't3a', who: 'linxiao', at: dayAt(1, 16, 2), text: '<@cheese> 把这周的进展整理成组会幻灯片，8 页以内' },
          { id: 't3k', mark: '林晓 开工 · 交给芝士', at: dayAt(1, 16, 3) },
          {
            id: 't3b',
            who: 'cheese',
            at: dayAt(1, 16, 40),
            text: '做好了 7 页：进展、两张结果表、截断实验的设计、下周计划。请你看一眼。',
          },
          { id: 't3c', who: 'linxiao', at: dayAt(1, 18, 10), text: '第 4 页的表换成 R@1/R@5 两列就行，别的都好' },
          { id: 't3d', mark: '林晓 采纳 · 已合进正式版', at: dayAt(1, 18, 12), ok: true },
        ],
        result:
          '**report/组会-11-07.pdf**，7 页：\n\n1. 本周进展\n2. CLIP 零样本检索结果\n3. 和论文的差距\n4. 长描述截断实验设计\n5. 下周计划',
      },
    ] as Thing[],
    chat: {
      'c-exp': [
        {
          id: 'e1',
          who: 'wanglei',
          at: dayAt(1, 16, 40),
          text: '今天把 ViT-B/32 的零样本检索跑完了，**Flickr30K R@1 = 78.2**，论文是 79.3，差 1.1 个点。',
        },
        { id: 'e2', who: 'wanglei', at: dayAt(1, 16, 41), text: '明天再看看是哪里不一样' },
        {
          id: 'e3',
          who: 'chensiyuan',
          at: dayAt(1, 17, 5),
          text: '我这边 COCO 也对不上，R@1 低了快 2 个点。你们 COCO 用的是 1K 还是 5K？',
        },
        {
          id: 'e4',
          who: 'zhaoyutong',
          at: dayAt(1, 17, 9),
          text: '5K，口径在《评测口径说明》里写了',
          reactions: [rx('👍', 'chensiyuan')],
        },
        {
          id: 'e5',
          who: 'wanglei',
          at: minsAgo(96),
          text: '找到一个可能的原因：项目里那份 `eval/preprocess.py` 是 `Resize(256)`，没有 center crop。我 notebook 里用的是 224 + crop。',
        },
        {
          id: 'e6',
          who: 'chensiyuan',
          at: minsAgo(90),
          text: '所以现在评测脚本其实有三份：\n- `eval/retrieval.py`（项目里的）\n- 王磊 notebook 里那份\n- 我的 `eval_coco.py`\n\n口径不统一，数字没法比 😂',
          reactions: [rx('😂', 'wanglei', 'zhaoyutong'), rx('👀', 'linxiao')],
        },
        { id: 'e7', who: 'zhaoyutong', at: minsAgo(78), text: '<@cheese> 统计一下 COCO 描述的长度分布', thing: 't1' },
      ],
      'c-papers': [
        {
          id: 'p1',
          who: 'cheese',
          at: dayAt(3, 15, 20),
          text: '**BLIP 精读笔记**放进项目文件了：`docs/papers/BLIP-精读.md`。\n\n要点：\n1. CapFilt：captioner 重写网页描述，filter 去掉噪声描述\n2. 文本端一份参数三种用法（ITC / ITM / LM）\n3. 零样本 Flickr30K R@1 = 94.8（ViT-L）',
        },
        { id: 'p2', who: 'wanglei', at: dayAt(1, 10, 30), text: '这周读 BLIP，我先把基线跑起来', thing: 't2' },
        { id: 'p3', who: 'chensiyuan', at: minsAgo(33), text: 'BLIP 表 5 的 COCO 结果是在 5K 上报的，别和 1K 搞混' },
        {
          id: 'p4',
          who: 'chensiyuan',
          at: minsAgo(32),
          text: '另外 ITM 的 hard negative 是按相似度从 batch 里采的，复现时要注意',
        },
      ],
      'c-meeting': [
        {
          id: 'g1',
          who: 'linxiao',
          at: dayAt(1, 16, 2),
          text: '<@cheese> 把这周的进展整理成组会幻灯片，8 页以内',
          thing: 't3',
        },
        {
          id: 'g2',
          who: 'zhouke',
          at: dayAt(1, 20, 15),
          text: '周五我请假，回来看纪要',
          reactions: [rx('👌', 'linxiao')],
        },
        { id: 'g3', who: 'linxiao', at: minsAgo(150), text: '周五组会改到下午 3 点，在 302。' },
      ],
      'c-general': [
        {
          id: 'z1',
          who: 'zhaoyutong',
          at: dayAt(2, 14, 3),
          text: 'A100 那台周三晚上 10 点停机维护，大概两小时，记得提前存 checkpoint。',
        },
        {
          id: 'z2',
          who: 'wanglei',
          at: dayAt(2, 14, 20),
          text: '收到',
          reactions: [rx('👍', 'chensiyuan', 'linxiao')],
        },
        {
          id: 'z3',
          who: 'zhouke',
          at: dayAt(1, 9, 48),
          text: '新买的 4T 硬盘装好了，挂在 /data2，COCO 原图我先拷过去了',
        },
        { id: 'z4', who: 'linxiao', at: minsAgo(20), text: '中期报告下周三交，<@zhaoyutong> 记得把数据那节补上' },
      ],
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
  if (center !== 'chan') {
    S.thing = null
    S.focus = false
  }
  if (center === 'chan') delete S.unread[S.chan]
}

export function openThing(id: string): void {
  const t = thingById(id)
  if (!t) return
  go('chan', { chan: t.chan, thing: id, rtab: 'chat' })
}

export function closeThing(): void {
  S.thing = null
  S.focus = false
}

let seq = 0
const nextId = (p: string) => `${p}${Date.now().toString(36)}${seq++}`

/** The channel composer: an @芝士 opens a 讨论 under the message; anything else is just said. */
export function send(): void {
  const text = S.typing.trim()
  if (!text) return
  S.typing = ''
  const at = new Date().toISOString()
  if (text.includes('<@cheese>') && !t4()) {
    const scripted = text === ASK
    S.askAt = at
    S.things.unshift({
      id: 't4',
      title: scripted
        ? '统一评测脚本，固定输出 R@1/R@5/R@10'
        : text
            .replace(/<@[^>]+>/g, '')
            .trim()
            .slice(0, 24),
      chan: S.chan,
      owner: ME,
      status: 'talk',
      replies: 1,
      last: scripted ? '芝士：两边预处理不一样，建议统一成一份' : '芝士：收到，我先看看项目里的相关文件',
      at,
    })
    S.chat[S.chan]!.push({ id: 'm4', who: ME, at, text, thing: 't4' })
    toast('在这条消息下开了一个讨论')
    return
  }
  S.chat[S.chan]!.push({ id: nextId('u'), who: ME, at, text, replyTo: S.replyTo ?? undefined })
  S.replyTo = null
}

/** The composer inside a 一件事: what 林晓 says lands there; 芝士 answers an @. */
export function sayInThing(): void {
  const t = thingById(S.thing)
  const text = S.thingTyping.trim()
  if (!t || !text) return
  S.thingTyping = ''
  const list = (S.extra[t.id] ??= [])
  list.push({ id: nextId('x'), who: ME, at: new Date().toISOString(), text, replyTo: S.thingReplyTo ?? undefined })
  S.thingReplyTo = null
  t.replies++
  t.last = `${NAMES[ME]}：${text.replace(/<@cheese>/g, '@芝士')}`
  t.at = new Date().toISOString()
  const asks = text.includes('<@cheese>') || t.status !== 'talk'
  if (!asks) return
  setTimeout(() => {
    const answer =
      t.status === 'talk'
        ? '好，记下了，开工时按这个来。'
        : t.status === 'run'
          ? '收到，我把这条加进计划，做完一起给你看。'
          : '收到。这件已经做完了，要改的话我在这份上接着改，改完再请你采纳。'
    list.push({ id: nextId('x'), who: 'cheese', at: new Date().toISOString(), text: answer })
    t.replies++
    t.last = `芝士：${answer}`
  }, 1400)
}

export function react(lineId: string, emoji: string): void {
  for (const lines of [...Object.values(S.chat), ...Object.values(S.extra)]) {
    const line = lines.find((l) => l.id === lineId)
    if (!line) continue
    const list = (line.reactions ??= [])
    const r = list.find((x) => x.emoji === emoji)
    if (!r) list.push({ emoji, count: 1, authors: [ME] })
    else if (r.authors.includes(ME)) {
      r.authors = r.authors.filter((a) => a !== ME)
      r.count--
      if (!r.count) list.splice(list.indexOf(r), 1)
    } else {
      r.authors.push(ME)
      r.count++
    }
    return
  }
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
  t.last = '芝士：开始改了，先对齐预处理'
  t.at = new Date().toISOString()
  S.morph = true
  S.rtab = 'site'
  S.live = 0
  toast('开工了，交给芝士')
  setTimeout(() => (S.morph = false), 1200)
}

export function finish(): void {
  const t = t4()
  if (!t) return
  t.status = 'wait'
  t.last = '芝士：Flickr30K R@1 = 79.1，等你采纳'
  t.at = new Date().toISOString()
  S.live = 5
}

export function accept(): void {
  const t = t4()
  if (!t || t.status !== 'wait') return
  t.status = 'done'
  t.last = '已采纳 · 合进正式版'
  t.at = new Date().toISOString()
  S.rtab = 'result'
  for (const h of S.history) h.fresh = false
  S.history.unshift({
    who: 'linxiao',
    what: '采纳了任务「统一评测脚本」：改了 2 个文件，新增 results/eval_results.md',
    at: new Date().toISOString(),
    fresh: true,
  })
  toast('已采纳：合进项目文件的正式版')
}

export function suggAccept(): void {
  if (S.suggAccepted) return
  S.suggAccepted = true
  for (const h of S.history) h.fresh = false
  S.history.unshift({
    who: 'linxiao',
    what: '接受了芝士对《组会纪要 11-07》的建议',
    at: new Date().toISOString(),
    fresh: true,
  })
  toast('建议已接受，进了正式版')
}

export function suggReject(): void {
  if (S.suggAccepted || S.suggRejected) return
  S.suggRejected = true
  for (const h of S.history) h.fresh = false
  S.history.unshift({
    who: 'linxiao',
    what: '拒绝了芝士对《组会纪要 11-07》的建议',
    at: new Date().toISOString(),
    fresh: true,
  })
  toast('建议已拒绝，正式版不变')
}

export function undo(index: number): void {
  const h = S.history[index]
  if (!h) return
  S.history.splice(index, 1)
  if (h.what.startsWith('接受了芝士对')) S.suggAccepted = false
  if (h.what.startsWith('拒绝了芝士对')) S.suggRejected = false
  toast(`已撤回：${h.what}`)
}
