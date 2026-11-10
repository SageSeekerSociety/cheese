// Everything the demo screen shows is read from this one reactive object, and
// every click (a person's or the scripted cursor's) goes through the actions
// below, so playing, stepping and clicking by hand always agree.
import type { ReactionAgg } from '@/cx_types'
import type { Submission } from './course'

import { reactive } from 'vue'

import {
  COURSE_CHAT,
  dueChip,
  hintFor,
  PROJECT_FILES,
  SET,
  topicById,
  TOPICS,
  UNITS,
  versionOf,
  welcome,
} from './course'
import {
  ASK,
  CHAN_DOCS,
  channelsOf,
  CLAIMED,
  type ClaimKey,
  CODE,
  dayAt,
  FILES,
  isClaim,
  ME,
  minsAgo,
  MINUTES_DOC,
  NAMES,
  type ProjectKey,
  projectOfChan,
} from './data'

import { monthDayTime } from '@/lib/dateText'

export type Status = 'talk' | 'run' | 'wait' | 'done'
export type Center = 'inbox' | 'chan' | 'files' | 'settings' | 'space'
/** The bottom panel's 输出 for one file. */
export type Out =
  | { kind: 'running' }
  | { kind: 'run'; cmd: string; out: string }
  | { kind: 'judging'; version: string }
  | { kind: 'judge'; sub: string }
export type ChanView = 'doc' | 'talk'

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
  /** A code save: the file and the text 正式版 had before it, which 撤回 puts back. */
  file?: string
  prev?: string
  /** Nothing to take back (领取 itself). */
  fixed?: boolean
}

export const PHASES = ['讨论', '在做', '等你采纳', '完成'] as const
export const PHASE: Record<Status, number> = { talk: 0, run: 1, wait: 2, done: 3 }

const rx = (emoji: string, ...authors: string[]): ReactionAgg => ({ emoji, count: authors.length, authors })

/** A 一件事 that is already finished: asked, started, accepted. */
function doneThing(id: string, title: string, owner: string, ask: string, answer: string, at: string): Thing {
  const t = Date.parse(at)
  const back = (m: number) => new Date(t - m * 60_000).toISOString()
  return {
    id,
    title,
    chan: 'c-exp',
    owner,
    status: 'done',
    replies: 2,
    last: '已采纳 · 合进正式版',
    at,
    talk: [
      { id: `${id}a`, who: owner, at: back(50), text: `<@cheese> ${ask}` },
      { id: `${id}k`, mark: `${NAMES[owner]} 开工 · 交给芝士`, at: back(48) },
      { id: `${id}b`, who: 'cheese', at: back(4), text: answer },
      { id: `${id}c`, mark: `${NAMES[owner]} 采纳 · 已合进正式版`, at, ok: true },
    ],
    result: answer,
  }
}

function initial() {
  return {
    /** Which space on the rail is open: the research project (the tour runs here) or the course. */
    project: 'research' as ProjectKey,
    /** The channel each project was last on, so switching back lands where you were. */
    chanOf: { research: 'c-exp', course: 'k-general', datalab: 'dl-general', week3: 'w3-general' } as Record<
      ProjectKey,
      string
    >,
    /** The file each project's 项目文件 was last on. */
    fileOf: { research: MINUTES_DOC } as Record<string, string>,
    /** The projects 林晓 has claimed from the course, in the order they were claimed (the rail's order). */
    claimed: [] as ClaimKey[],
    center: 'chan' as Center,
    chan: 'c-exp',
    thing: null as string | null,
    rtab: 'chat',
    /** 频道详情 is a popover on the channel title; the right panel holds only an opened 一件事. */
    chanDetails: false,
    /** Each channel's center view. Unset until the channel is first entered: a channel
     *  with a backlog (unread ≥ 2) opens on its 文档, any other on 对话. */
    chanView: { 'c-exp': 'talk' } as Record<string, ChanView>,
    /** The part of each channel's 文档 people wrote: a person's edit applies at once. */
    chanDocs: structuredClone(CHAN_DOCS),
    /** A message to bring into view and flash in the channel's 对话 (from 文档's ↗ or a 一件事's 来自). */
    jumpId: null as string | null,
    jumpTick: 0,
    /** 专注：the open 一件事 takes the center column too. */
    focus: false,
    unread: { 'c-papers': 2, 'c-general': 1, 'k-general': 1, 'k-qa': 3 } as Record<string, number>,
    /** 项目文件's folded folders, per project (a 题单 folds by problem). */
    collapsed: {} as Record<string, string[]>,
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
    /** Code a person merged into the research project's official version by hand: path → its lines. */
    codeEdits: {} as Record<string, string[]>,
    /** Edits sent for someone else's 采纳 while 代码受保护 is on: path → its lines. */
    codePending: {} as Record<string, string[]>,
    /** The research code file open in the editor, on the person's own copy. */
    codeDraft: null as { path: string; text: string } | null,
    /** A claimed project's code files as they are now, keyed by fkey. */
    official: {} as Record<string, string>,
    /** What is typed in a claimed project's editor and not saved yet, keyed by fkey. */
    unsaved: {} as Record<string, string>,
    /** The code view's bottom panel: which tab, and each file's 输出. */
    outTab: 'out' as 'out' | 'term',
    out: {} as Record<string, Out>,
    /** Each project's terminal transcript (the person's own environment). */
    term: {} as Record<string, { cmd: string; out: string }[]>,
    /** Every 提交评测 in each claimed project, newest first. */
    subs: { datalab: [], week3: [] } as Record<ClaimKey, Submission[]>,
    /** The file being judged right now (fkey), if any. */
    judging: null as string | null,
    /** A submission whose pinned version is open (read-only) in the code view. */
    viewSub: null as string | null,
    /** When the scripted ask went out (the 一件事's own clock counts from it). */
    askAt: '',
    /** What 林晓 said by hand inside each 一件事, after the script's lines. */
    extra: {} as Record<string, ChatLine[]>,
    /** Each project's 历史: every change that took effect on its 正式版. */
    history: {
      course: [],
      datalab: [],
      week3: [],
      research: [
        { who: 'wanglei', what: '改了《组会纪要 11-07》', at: minsAgo(9) },
        { who: 'chensiyuan', what: '采纳了任务「补长描述截断实验」：改了 2 个文件', at: dayAt(1, 17, 42) },
        { who: 'zhaoyutong', what: '上传了 report/中期报告.docx', at: dayAt(1, 11, 5) },
        { who: 'wanglei', what: '采纳了任务「BLIP 精读笔记」：新增 docs/papers/BLIP-精读.md', at: dayAt(3, 15, 20) },
      ],
    } as Record<ProjectKey, HistoryEntry[]>,
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
      {
        id: 't5',
        title: '长描述：截到 77 还是分段编码',
        chan: 'c-exp',
        owner: 'chensiyuan',
        status: 'talk',
        replies: 2,
        last: '芝士：两种都能做，分段编码要多算约 2.3 倍',
        at: minsAgo(63),
        talk: [
          {
            id: 't5a',
            who: 'chensiyuan',
            at: minsAgo(64),
            text: '<@cheese> 超过 77 个 token 的描述，直接截断和分段编码再平均，哪个更合理？',
          },
          {
            id: 't5b',
            who: 'cheese',
            at: minsAgo(63),
            text: '两种都能做。截断最简单，和论文一致；分段编码再平均能用上后半句，但要多算约 2.3 倍。建议先用截断出主表，分段编码做一组对照。',
          },
        ],
      },
      doneThing(
        't6',
        '对齐 Flickr30K 1K 测试集划分',
        'zhaoyutong',
        '按 Karpathy split 把 Flickr30K 的 1K 测试集整理出来',
        '整理好了：`data/flickr30k/test_1k.json`，1000 张图、5000 条描述，和论文用的划分一致。',
        dayAt(2, 15, 30)
      ),
      doneThing(
        't7',
        'CLIP ViT-B/32 零样本基线',
        'wanglei',
        '用 ViT-B/32 跑一遍 Flickr30K 零样本检索',
        'Flickr30K 1K 上 R@1 = 78.2，论文是 79.3。结果在 `results/clip_zeroshot.md`。',
        dayAt(1, 16, 38)
      ),
      doneThing(
        't8',
        '下载 COCO 2017 并校验',
        'zhouke',
        '把 COCO 2017 的 val 和标注下载下来，校验一下 md5',
        '下载完了，放在 `/data/coco2017`，两个压缩包的 md5 都对得上。',
        dayAt(3, 11, 2)
      ),
      doneThing(
        't9',
        '画 R@K 曲线的脚本',
        'chensiyuan',
        '写个脚本，把结果表画成 R@1/5/10 的曲线',
        '写好了：`scripts/plot_recall.py`，读 `results/` 下的表，一张图一个测试集。',
        dayAt(4, 14, 20)
      ),
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
        {
          id: 'e8',
          who: 'chensiyuan',
          at: minsAgo(64),
          text: '<@cheese> 超过 77 个 token 的描述，直接截断和分段编码再平均，哪个更合理？',
          thing: 't5',
        },
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
      ...structuredClone(COURSE_CHAT),
    } as Record<string, ChatLine[]>,
    showNotes: false,
  }
}

export const S = reactive(initial())

/** Bumped on every reset, so a timer started before it does not land in the fresh state. */
let gen = 0
const later = (ms: number, fn: () => void) => {
  const my = gen
  setTimeout(() => my === gen && fn(), ms)
}

export function reset(): void {
  const keep = S.showNotes
  gen++
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
  if (center === 'chan') {
    // Decided on entry, before the unread count is cleared: a backlog lands on 文档.
    S.chanView[S.chan] ??= (S.unread[S.chan] ?? 0) >= 2 ? 'doc' : 'talk'
    delete S.unread[S.chan]
  }
}

/** The rail: switch to another project, back on the channel it was last on. The course
 *  space opens on its own page, the 题目 list. */
export function switchProject(p: ProjectKey): void {
  if (S.project !== p) {
    if (S.center === 'chan') S.chanOf[S.project] = S.chan
    S.fileOf[S.project] = S.file
    // What was half-typed belongs to the channel it was typed in, not to the other project.
    S.typing = ''
    S.replyTo = null
    S.viewSub = null
    S.codeDraft = null
    S.project = p
    S.file = S.fileOf[p] ?? S.file
    S.ftab = isClaim(p) ? 'subs' : 'pending'
    S.settingsSection = p === 'course' ? 'course' : 'project'
  }
  if (p === 'course') go('space', { chan: S.chanOf.course })
  else go('chan', { chan: S.chanOf[p], thing: null })
}

/** Unread messages in one space's channels (the rail's count). */
export const unreadIn = (p: ProjectKey) => channelsOf(p).reduce((n, c) => n + (S.unread[c.id] ?? 0), 0)

export function openThing(id: string): void {
  const t = thingById(id)
  if (!t) return
  if (projectOfChan(t.chan) !== S.project) switchProject(projectOfChan(t.chan))
  go('chan', { chan: t.chan, thing: id, rtab: 'chat' })
}

export function closeThing(): void {
  S.thing = null
  S.focus = false
}

/** Show one message of a channel in its 对话: the timeline scrolls it into view and flashes it. */
export function jumpTo(chan: string, lineId: string): void {
  const wasFocus = S.focus && !!S.thing
  S.focus = false
  if (S.center !== 'chan' || S.chan !== chan)
    go('chan', { chan, thing: S.thing && thingById(S.thing)?.chan === chan ? S.thing : null })
  S.chanView[chan] = 'talk'
  const fire = () => {
    S.jumpId = lineId
    S.jumpTick++
  }
  // Leaving 专注 animates the center column back in; scroll once it has its width.
  if (wasFocus) setTimeout(fire, 450)
  else fire()
}

/** Put an open question from a channel's 文档 into the channel's composer as an @芝士 message:
 *  sending it opens the 讨论 the normal way. */
export function startFromDoc(chan: string, text: string): void {
  go('chan', { chan, thing: null })
  S.chanView[chan] = 'talk'
  S.typing = `<@cheese> ${text.replace(/`/g, '')}`
}

/** A person's edit to the part of a channel's 文档 people wrote: it applies at once. */
export function editChanDoc(chan: string, html: string): void {
  const doc = S.chanDocs[chan]
  if (!doc || doc.html === html) return
  Object.assign(doc, { html, by: ME, at: new Date().toISOString() })
}

/** The channel message a 一件事 was opened from. */
export function sourceOf(thingId: string): ChatLine | null {
  for (const lines of Object.values(S.chat)) {
    const l = lines.find((x) => x.thing === thingId)
    if (l) return l
  }
  return null
}

// ---- code ----
//
// The research project keeps its own flow: 改代码 edits a copy of the official
// version, 合进正式版 puts it in (or 请人采纳 while 代码受保护 is on). In a
// project claimed from the course, a person's edit is saved straight into the
// project's files; every save is a 历史 entry with 撤回.

/** The key of one project's file in the per-file records. */
export const fkey = (p: ProjectKey, path: string) => `${p}:${path}`

/** The official text of a research code file: a person's merged edit, else the task's version once accepted. */
export function officialCode(path: string): string[] {
  const c = CODE[path]
  if (!c) return []
  return S.codeEdits[path] ?? (t4()?.status === 'done' && c.after ? c.after : c.before)
}

/** A code file as it is now, in any project. */
export function officialText(p: ProjectKey, path: string): string {
  return p === 'research' ? officialCode(path).join('\n') : S.official[fkey(p, path)] ?? ''
}

/** What the editor shows: the research copy being edited, or a claimed file with what is typed and not saved. */
export function draftText(p: ProjectKey, path: string): string {
  if (p === 'research') return S.codeDraft?.path === path ? S.codeDraft.text : officialText(p, path)
  return S.unsaved[fkey(p, path)] ?? officialText(p, path)
}

export function typeCode(p: ProjectKey, path: string, text: string): void {
  if (p === 'research') {
    if (S.codeDraft?.path === path) S.codeDraft.text = text
    return
  }
  const k = fkey(p, path)
  if (text === officialText(p, path)) delete S.unsaved[k]
  else S.unsaved[k] = text
}

export const isUnsaved = (p: ProjectKey, path: string) => fkey(p, path) in S.unsaved

/** The judged unit a file belongs to (Data Lab's bits.c, a 题单 problem's main.cpp), if any. */
export const unitOf = (p: ProjectKey, path: string) =>
  isClaim(p) ? PROJECT_FILES[p].find((f) => f.path === path)?.unit : undefined

function log(p: ProjectKey, who: string, what: string, extra: Partial<HistoryEntry> = {}): void {
  const list = S.history[p]
  for (const h of list) h.fresh = false
  list.unshift({ who, what, at: new Date().toISOString(), fresh: true, ...extra })
}

/** 保存 (⌘S) in a claimed project: the file changes, and 历史 records it with what it read before. */
export function saveCode(p: ProjectKey, path: string, quiet = false): void {
  const k = fkey(p, path)
  const text = S.unsaved[k]
  if (text === undefined || p === 'research') return
  const prev = officialText(p, path)
  S.official[k] = text
  delete S.unsaved[k]
  log(p, ME, `改了 ${path}`, { file: path, prev })
  if (!quiet) toast(`已保存：${path}`)
}

/** How many lines a research copy changes against the official version (lines only on one side, the larger side). */
export function changedLines(path: string, lines: string[]): number {
  const count = (xs: string[]) => xs.reduce((m, x) => m.set(x, (m.get(x) ?? 0) + 1), new Map<string, number>())
  const diff = (a: Map<string, number>, b: Map<string, number>) =>
    [...a].reduce((n, [k, v]) => n + Math.max(0, v - (b.get(k) ?? 0)), 0)
  const a = count(officialCode(path))
  const b = count(lines)
  return Math.max(diff(a, b), diff(b, a))
}

export function editCode(path: string): void {
  S.codeDraft = { path, text: officialCode(path).join('\n') }
}

/** 合进正式版: a person's edit takes effect at once, unless the project has 代码受保护 on. */
export function mergeCode(): void {
  const d = S.codeDraft
  if (!d) return
  const lines = d.text.replace(/\n+$/, '').split('\n')
  S.codeDraft = null
  if (S.settingsProtectCode) {
    S.codePending[d.path] = lines
    toast('已请人采纳：别人采纳之后才进正式版')
    return
  }
  S.codeEdits[d.path] = lines
  delete S.codePending[d.path]
  log('research', ME, `改了 ${d.path}`)
  toast('已合进正式版')
}

// ---- 运行 and the terminal: the person's own environment ----

/** The research project's files, run on the copy as it now reads: a short, plausible run. */
function researchRun(path: string, text: string): { cmd: string; out: string } {
  if (path === 'eval/preprocess.py') {
    const crop = /CenterCrop\(\s*224\s*\)/.test(text)
    const size = /Resize\(\s*(\d+)/.exec(text)?.[1] ?? '256'
    const shape = crop ? '[3, 224, 224]' : `[3, ${size}, ${Math.round((Number(size) * 4) / 3)}]`
    return {
      cmd: 'python -c "from eval.preprocess import preprocess; ..." data/coco/val2017/000000000139.jpg',
      out: `torch.Size(${shape})  dtype=torch.float32\n${crop ? '和模型输入 224×224 一致' : '注意：不是 224×224，模型会在里面再缩放一次'}`,
    }
  }
  if (path === 'eval/retrieval.py') {
    const all = text.includes('recall_at_k')
    return {
      cmd: 'python -m eval.retrieval --data flickr30k --split 1k --limit 100',
      out: `encoding 100 images, 500 captions … done (3.8s)\n${all ? 'R@1 79.0  R@5 94.0  R@10 97.0' : 'R@1 78.0'}   （只抽了前 100 张，快速检查）`,
    }
  }
  if (path.endsWith('.yaml')) {
    const bs = /batch_size:\s*(\d+)/.exec(text)?.[1] ?? '?'
    return {
      cmd: `python -c "import yaml; print(yaml.safe_load(open('${path}'))['data'])"`,
      out: `{'flickr30k': '/data/flickr30k', 'coco': '/data/coco2017', 'batch_size': ${bs}, 'num_workers': 8}`,
    }
  }
  return { cmd: `bash -n ${path}`, out: '语法检查通过（只检查，不下载）' }
}

function runOf(p: ProjectKey, path: string, text: string) {
  const unit = unitOf(p, path)
  return unit ? UNITS[unit]!.run(text) : researchRun(path, text)
}

/** 运行: on my copy, in my own environment; the output lands in the bottom panel and the terminal. */
export function runCode(p: ProjectKey, path: string): void {
  const k = fkey(p, path)
  const text = draftText(p, path)
  S.out[k] = { kind: 'running' }
  S.outTab = 'out'
  later(800, () => {
    const r = runOf(p, path, text)
    S.out[k] = { kind: 'run', ...r }
    ;(S.term[p] ??= []).push(r)
  })
}

/** A command typed into the terminal. */
export function termRun(p: ProjectKey, path: string, cmd: string): void {
  const list = (S.term[p] ??= [])
  const c = cmd.trim()
  if (!c) return
  if (c === 'clear') {
    list.splice(0)
    return
  }
  const files = isClaim(p) ? PROJECT_FILES[p].map((f) => f.path) : FILES.map((f) => f.path)
  if (c === 'ls') {
    list.push({ cmd: c, out: [...new Set(files.map((f) => f.split('/')[0]))].join('  ') })
    return
  }
  const cat = /^cat\s+(\S+)$/.exec(c)
  if (cat) {
    const f = cat[1]!
    const code = isClaim(p) ? PROJECT_FILES[p].find((x) => x.path === f)?.kind === 'code' : !!CODE[f]
    list.push({
      cmd: c,
      out: code ? draftText(p, f) : files.includes(f) ? '（文档，在项目文件里打开）' : `cat: ${f}: 没有这个文件`,
    })
    return
  }
  if (/^(make|\.\/|g\+\+|python|bash)/.test(c)) {
    list.push({ cmd: c, out: runOf(p, path, draftText(p, path)).out })
    return
  }
  list.push({ cmd: c, out: `${c.split(/\s/)[0]}：演示里的终端只认 ls、cat、clear 和运行命令` })
}

// ---- 提交评测: the judge runs on a pinned version of 正式版 ----

let seqSub = 0

/** 提交评测 on a judged file: the file as it is now is pinned (its version id) and judged. */
export function submitCode(p: ProjectKey, path: string): void {
  const unit = unitOf(p, path)
  if (!unit || !isClaim(p) || S.judging) return
  // What is typed is saved first, so the judged version is the file as it reads now.
  saveCode(p, path, true)
  const k = fkey(p, path)
  const text = officialText(p, path)
  const version = versionOf(text)
  S.judging = k
  S.out[k] = { kind: 'judging', version }
  S.outTab = 'out'
  later(1500, () => {
    S.judging = null
    const v = UNITS[unit]!.judge(text)
    const n = S.subs[p].filter((x) => x.unit === unit).length + 1
    const sub: Submission = { ...v, id: `s${seqSub++}`, unit, n, at: new Date().toISOString(), version, code: text }
    S.subs[p].unshift(sub)
    S.out[k] = { kind: 'judge', sub: sub.id }
    S.ftab = 'subs'
    toast(v.score === v.total ? `通过了：${UNITS[unit]!.title}` : `${v.score}/${v.total} · 第 ${n} 次提交`)
  })
}

/** The latest submission of each unit decides where it stands (以截止前最后一次为准). */
export const latestOf = (p: ClaimKey, unit: string) => S.subs[p].find((x) => x.unit === unit)

// ---- 项目文件 ----

/** Open a file in 项目文件, unfolding its folder. */
export function openFile(path: string, extra: Partial<typeof S> = {}): void {
  const dir = path.includes('/') ? path.slice(0, path.indexOf('/')) : null
  const list = S.collapsed[S.project]
  if (dir && list?.includes(dir)) list.splice(list.indexOf(dir), 1)
  go('files', { file: path, viewSub: null, ...extra })
}

/** Open the exact version a submission judged, read-only. */
export function openVersion(id: string): void {
  if (!isClaim(S.project)) return
  const sub = S.subs[S.project].find((x) => x.id === id)
  if (!sub) return
  openFile(UNITS[sub.unit]!.path, { viewSub: id, ftab: 'subs' })
}

// ---- 领取: a 题目 becomes a project of my own ----

export function claim(topicId: string): void {
  const t = topicById(topicId)
  if (!t?.project) {
    toast('演示里能领取的是 Data Lab 和第 3 周题单')
    return
  }
  const p = t.project
  if (!S.claimed.includes(p)) {
    S.claimed.push(p)
    const files = PROJECT_FILES[p]
    for (const f of files) if (f.kind === 'code') S.official[fkey(p, f.path)] = f.starter!
    S.chat[CLAIMED[p].chan] = welcome(p, monthDayTime(Date.parse(t.due)))
    S.chanView[CLAIMED[p].chan] = 'talk'
    S.collapsed[p] = p === 'week3' ? SET.map((x) => x.id) : []
    S.fileOf[p] = files[0]!.path
    S.history[p] = [
      {
        who: ME,
        what: `领取了「${t.title}」：新建了 ${p === 'datalab' ? 'README.md 和 bits.c' : '题单.md 和 5 道题的文件'}`,
        at: new Date().toISOString(),
        fixed: true,
      },
    ]
    toast(`领取了「${t.title}」：你的项目「${CLAIMED[p].name}」建好了`)
  }
  switchProject(p)
}

let seq = 0
const nextId = (p: string) => `${p}${Date.now().toString(36)}${seq++}`

/** The channel composer: an @芝士 opens a 讨论 under the message; anything else is just said. */
export function send(): void {
  const text = S.typing.trim()
  if (!text) return
  S.typing = ''
  const at = new Date().toISOString()
  // In the course and its projects 芝士 only gives hints, in the channel: no 一件事 is opened there.
  // A plain 「@芝士」 counts too: a viewer typing it by hand need not pick it from the mention list.
  if (/<@cheese>|@芝士/.test(text) && S.project !== 'research') {
    const id = nextId('u')
    const chan = S.chan
    S.chat[chan]!.push({ id, who: ME, at, text, replyTo: S.replyTo ?? undefined })
    S.replyTo = null
    later(1200, () =>
      S.chat[chan]!.push({
        id: nextId('c'),
        who: 'cheese',
        at: new Date().toISOString(),
        text: hintFor(text),
        replyTo: id,
      })
    )
    return
  }
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
  log('research', ME, '采纳了任务「统一评测脚本」：改了 2 个文件，新增 results/eval_results.md')
  toast('已采纳：合进项目文件的正式版')
}

export function suggAccept(): void {
  if (S.suggAccepted) return
  S.suggAccepted = true
  log('research', ME, '接受了芝士对《组会纪要 11-07》的建议')
  toast('建议已接受，进了正式版')
}

export function suggReject(): void {
  if (S.suggAccepted || S.suggRejected) return
  S.suggRejected = true
  log('research', ME, '拒绝了芝士对《组会纪要 11-07》的建议')
  toast('建议已拒绝，正式版不变')
}

/** 撤回 one entry of the open project's 历史: a code save puts back the text the file had before it. */
export function undo(index: number): void {
  const list = S.history[S.project]
  const h = list[index]
  if (!h || h.fixed) return
  list.splice(index, 1)
  if (h.what.startsWith('接受了芝士对')) S.suggAccepted = false
  if (h.what.startsWith('拒绝了芝士对')) S.suggRejected = false
  if (h.file && h.prev !== undefined) S.official[fkey(S.project, h.file)] = h.prev
  else if (S.project === 'research' && h.who === ME && h.what.startsWith('改了 ')) delete S.codeEdits[h.what.slice(3)]
  toast(`已撤回：${NAMES[h.who]} ${h.what}`)
}

// ---- 需要我的: deadlines across every project ----

export interface Deadline {
  id: string
  title: string
  sub: string
  chip: string
  open: () => void
}

/** A 题目 due within a week that is not done: not claimed yet, not submitted, or problems still open. */
export function deadlines(): Deadline[] {
  const out: Deadline[] = []
  for (const t of TOPICS) {
    const chip = dueChip(t.due)
    if (!chip) continue
    const p = t.project
    if (!p || !S.claimed.includes(p)) {
      out.push({ id: t.id, title: `${t.title} ${chip}`, sub: '还没领取', chip, open: () => switchProject('course') })
      continue
    }
    if (p === 'datalab') {
      const last = latestOf(p, 'datalab')
      if (last && last.score === last.total) continue
      out.push({
        id: t.id,
        title: `${t.title} ${chip}`,
        sub: last ? `最近一次 ${last.score}/${last.total}` : '还没提交',
        chip,
        open: () => {
          switchProject(p)
          openFile('bits.c')
        },
      })
    } else {
      const left = SET.filter((x) => {
        const last = latestOf(p, x.id)
        return !last || last.score < last.total
      }).length
      if (!left) continue
      out.push({
        id: t.id,
        title: `${t.title}：还差 ${left} 题`,
        sub: `${SET.length - left}/${SET.length} 题通过`,
        chip,
        open: () => {
          switchProject(p)
          openFile('题单.md')
        },
      })
    }
  }
  return out
}
