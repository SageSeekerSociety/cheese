// Everything the demo screen shows is read from this one reactive object, and
// every click (a person's or the scripted cursor's) goes through the actions
// below, so playing, stepping and clicking by hand always agree.
import type { ReactionAgg } from '@/cx_types'
import type { Submission } from './course'

import { reactive } from 'vue'

import { dueChip, hintFor, PROJECT_FILES, SET, topicById, TOPICS, UNITS, versionOf, welcome } from './course'
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
  isShared,
  ME,
  minsAgo,
  MINUTES_DOC,
  NAMES,
  type ProjectKey,
  projectOfChan,
  teamOf,
} from './data'

import { monthDayTime } from '@/lib/dateText'

export type Status = 'talk' | 'run' | 'wait' | 'done'
/** inbox: 待办 on 首页, or 需要我的 inside a project (the same cross-project list). space: the
 *  course 空间's pages. team: a team's 项目 list on 首页. The others are a project's. */
export type Center = 'inbox' | 'chan' | 'files' | 'settings' | 'space' | 'team'
export type SpacePage = 'tasks' | 'announce' | 'library'
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
  /** A change to several code files at once (an accepted task, a merged branch): path → text before. */
  prevs?: Record<string, string>
  /** Nothing to take back (领取 itself). */
  fixed?: boolean
}

export const PHASES = ['讨论', '在做', '等你采纳', '完成'] as const
export const PHASE: Record<Status, number> = { talk: 0, run: 1, wait: 2, done: 3 }

/** The key of one project's file in the per-file records. */
export const fkey = (p: ProjectKey, path: string) => `${p}:${path}`

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
    /** 首页 (its 待办, a team's projects, the course 空间 reached from it) or a project on the rail. */
    area: 'project' as 'home' | 'project',
    /** The project open (or last open, while on 首页): the research project is where the tour runs. */
    project: 'research' as ProjectKey,
    /** The course 空间's page: 题目 (all, or one 分类), 公告 or 资料库. */
    spacePage: 'tasks' as SpacePage,
    spaceCat: null as string | null,
    /** A 题目 to bring into view and flash in the list (from a project's 「来自」 line or a deadline). */
    spaceFlash: null as string | null,
    spaceFlashTick: 0,
    /** 全部题目's 「上次查看后新发布」 count: cleared once the list has been seen and left. */
    spaceNew: 3,
    /** 领取 asking 「自己领」 or 「和团队一起领」 for this 题目. */
    claimAsk: null as string | null,
    /** The team whose 项目 list is open on 首页. */
    teamView: 'linxiao',
    /** The channel each project was last on, so switching back lands where you were. */
    chanOf: { research: 'c-exp', datalab: 'dl-general', week3: 'w3-general', shelllab: 'sh-general' } as Record<
      ProjectKey,
      string
    >,
    /** The file each project's 项目文件 was last on. */
    fileOf: { research: MINUTES_DOC } as Record<string, string>,
    /** Where each project was left (channel, 项目文件 or 设置): switching back lands there. A project
     *  not visited yet lands on its default (the research project on its channel, a claimed
     *  题目 in 项目文件 on its 题面 / 题单). */
    centerOf: {} as Partial<Record<ProjectKey, Center>>,
    /** The projects claimed from the course (by 林晓 alone, or with her team), in the order they were claimed (the rail's order). */
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
    unread: { 'c-papers': 2, 'c-general': 1 } as Record<string, number>,
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
    /** 正式版受保护, per shared project: people also work on a branch, and someone else has to look before it merges. */
    protectMain: {} as Record<string, boolean>,
    settingsNotify: 'mentions',
    settingsRuntime: 'a100',
    /** Every project's code files as 正式版 reads now, keyed by fkey. People type straight into it. */
    official: Object.fromEntries(
      Object.entries(CODE).map(([path, c]) => [fkey('research', path), c.before.join('\n')])
    ),
    /** A file being typed into: the text it had before this burst of typing. The burst becomes one
     *  历史 entry once typing pauses (or on ⌘S, leaving the file, 运行, 提交评测). */
    snapBase: {} as Record<string, string>,
    /** 林晓's branch in a shared project (the research project, a team's 题目), if she opened one: her own
     *  copy of the code files it touches, merged back by 合回正式版. */
    branch: null as null | {
      project: ProjectKey
      name: string
      files: Record<string, string>
      asked: boolean
      approved: boolean
    },
    /** Lines selected in the open code file: 问芝士 sends them along. */
    codeSel: null as { path: string; start: number; end: number } | null,
    /** Lines to bring into view and mark in the editor (a file mention clicked in a channel). */
    codeAt: null as { path: string; start: number; end: number; tick: number } | null,
    /** What is typed in the open file's 问芝士 box. */
    asking: '',
    /** The code view's bottom panel: which tab, and each file's 输出. */
    outTab: 'out' as 'out' | 'term',
    out: {} as Record<string, Out>,
    /** Each project's terminal transcript (the person's own environment). */
    term: {} as Record<string, { cmd: string; out: string }[]>,
    /** Every 提交评测 in each claimed project, newest first. */
    subs: { datalab: [], week3: [], shelllab: [] } as Record<ClaimKey, Submission[]>,
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
      datalab: [],
      week3: [],
      shelllab: [],
      research: [
        { who: 'wanglei', what: '改了《组会纪要 11-07》', at: minsAgo(9) },
        {
          who: 'wanglei',
          what: '改了 configs/clip_vit_b32.yaml',
          at: minsAgo(26),
          file: 'configs/clip_vit_b32.yaml',
          prev: CODE['configs/clip_vit_b32.yaml']!.before.join('\n').replace('batch_size: 256', 'batch_size: 128'),
        },
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
          text: '找到一个可能的原因：项目里那份 `eval/preprocess.py:6` 是 `Resize(256)`，没有 center crop。我 notebook 里用的是 224 + crop。',
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

/** Leaving a project (for another, or for 首页): remember where it was, close what was half-typed. */
function leaveProject(): void {
  // 全部题目's 「新发布」 count is what was new at this visit: it clears once the list is left.
  if (S.center === 'space' && S.spacePage === 'tasks' && !S.spaceCat) S.spaceNew = 0
  if (S.area !== 'project') return
  if (S.center === 'chan') S.chanOf[S.project] = S.chan
  // 需要我的 belongs to no project: leaving from it keeps where the project itself was left.
  if (S.center !== 'inbox') S.centerOf[S.project] = S.center
  S.fileOf[S.project] = S.file
  snapshotAll()
  // What was half-typed belongs to the channel it was typed in, not to the other project.
  S.typing = ''
  S.replyTo = null
  S.viewSub = null
  S.codeSel = null
  S.asking = ''
}

/** The rail: switch to a project, back where it was last left. */
export function switchProject(p: ProjectKey): void {
  // The rail item of the project already open: stay where it is (from 需要我的, back into the project).
  if (S.area === 'project' && S.project === p && S.center !== 'inbox') return
  leaveProject()
  if (S.project !== p) {
    S.project = p
    // The channel this project was last on, even when it lands on 项目文件 (设置 names it).
    S.chan = S.chanOf[p]
    S.file = S.fileOf[p] ?? S.file
    S.ftab = isClaim(p) ? 'subs' : 'pending'
    S.settingsSection = 'project'
  }
  S.area = 'project'
  S.claimAsk = null
  // Where it was left; the first visit lands on the project's own starting place.
  const center = S.centerOf[p] ?? (isClaim(p) ? 'files' : 'chan')
  if (center === 'chan') go('chan', { chan: S.chanOf[p], thing: null })
  else if (center === 'files') go('files', { file: S.file, viewSub: null })
  else go(center)
}

/** The rail's 首页: 待办 and the home list (teams, 空间). */
export function openHome(center: 'inbox' | 'team' = 'inbox', extra: Partial<typeof S> = {}): void {
  leaveProject()
  S.area = 'home'
  S.claimAsk = null
  go(center, extra)
}

/** The course 空间, from 首页's list, a project's 「来自」 line or a deadline: one of its pages. */
export function openSpace(page: SpacePage = 'tasks', extra: Partial<typeof S> = {}): void {
  leaveProject()
  S.area = 'home'
  if (page !== 'tasks' || S.spacePage !== 'tasks') S.claimAsk = null
  go('space', { spacePage: page, spaceCat: null, ...extra })
}

/** Bring one 题目 into view in the space's list and flash it. */
export function showTopic(id: string): void {
  openSpace('tasks', { spaceFlash: id })
  S.spaceFlashTick++
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
// People edit 正式版 live, code as well as documents: what is typed is the file,
// in every project. History is automatic: each burst of typing becomes one 历史
// entry once it pauses, and 撤回 puts back what the file read before it.
// 芝士 never types into 正式版: a task works on its own copy and a person 采纳s
// it. A person who wants isolation opens a branch (research project), and
// 合回正式版 brings it back; with 正式版受保护 on, people have to work on a branch
// and someone else looks before it merges.

/** A code file as 正式版 reads now, in any project. */
export function officialText(p: ProjectKey, path: string): string {
  return S.official[fkey(p, path)] ?? ''
}

/** On a branch, the project's code files read from it. */
export const onBranch = (p: ProjectKey) => S.branch?.project === p

/** 正式版 is protected and no branch is open: the shared project's editor is read-only. */
export const lockedMain = (p: ProjectKey) => isShared(p) && !!S.protectMain[p] && !onBranch(p)

/** Who looks at a branch before it merges: someone else in the project. */
export const reviewerOf = (p: ProjectKey) => (p === 'research' ? 'wanglei' : 'zhouke')

/** What the editor shows: the branch's copy while on one, else 正式版. */
export function codeText(p: ProjectKey, path: string): string {
  if (onBranch(p)) return S.branch!.files[path] ?? officialText(p, path)
  return officialText(p, path)
}

const snapTimers: Record<string, number> = {}

/** Typing: on a branch it changes the branch's copy; otherwise 正式版 itself, at once. */
export function typeCode(p: ProjectKey, path: string, text: string): void {
  if (onBranch(p)) {
    S.branch!.files[path] = text
    return
  }
  if (lockedMain(p)) return
  const k = fkey(p, path)
  const prev = officialText(p, path)
  if (text === prev) return
  if (!(k in S.snapBase)) S.snapBase[k] = prev
  S.official[k] = text
  window.clearTimeout(snapTimers[k])
  const my = gen
  snapTimers[k] = window.setTimeout(() => my === gen && snapshot(p, path), 1500)
}

/** A burst of typing still waiting to become its 历史 entry. */
export const isSaving = (p: ProjectKey, path: string) => fkey(p, path) in S.snapBase

/** The judged unit a file belongs to (Data Lab's bits.c, a 题单 problem's main.cpp), if any. */
export const unitOf = (p: ProjectKey, path: string) =>
  isClaim(p) ? PROJECT_FILES[p].find((f) => f.path === path)?.unit : undefined

function log(p: ProjectKey, who: string, what: string, extra: Partial<HistoryEntry> = {}): void {
  const list = S.history[p]
  for (const h of list) h.fresh = false
  list.unshift({ who, what, at: new Date().toISOString(), fresh: true, ...extra })
}

/** The burst of typing on one file becomes one 历史 entry, with what the file read before it. */
export function snapshot(p: ProjectKey, path: string, quiet = true): void {
  const k = fkey(p, path)
  window.clearTimeout(snapTimers[k])
  const prev = S.snapBase[k]
  if (prev === undefined) return
  delete S.snapBase[k]
  if (prev !== officialText(p, path)) log(p, ME, `改了 ${path}`, { file: path, prev })
  if (!quiet) toast(`已记进历史：${path}`)
}

/** Every pending burst becomes its entry (leaving a project, before 撤回). */
export function snapshotAll(): void {
  for (const k of Object.keys(S.snapBase)) {
    const i = k.indexOf(':')
    snapshot(k.slice(0, i) as ProjectKey, k.slice(i + 1))
  }
}

/** How many lines differ between two versions of a file (lines only on one side, the larger side). */
export function changedLines(before: string[], after: string[]): number {
  const count = (xs: string[]) => xs.reduce((m, x) => m.set(x, (m.get(x) ?? 0) + 1), new Map<string, number>())
  const diff = (a: Map<string, number>, b: Map<string, number>) =>
    [...a].reduce((n, [k, v]) => n + Math.max(0, v - (b.get(k) ?? 0)), 0)
  const a = count(before)
  const b = count(after)
  return Math.max(diff(a, b), diff(b, a))
}

// ---- a person's branch (a shared project) ----

/** The branch's files that differ from 正式版 now: path → changed line count. */
export function branchChanges(): { path: string; n: number }[] {
  const b = S.branch
  if (!b) return []
  return Object.entries(b.files)
    .map(([path, text]) => ({ path, n: changedLines(officialText(b.project, path).split('\n'), text.split('\n')) }))
    .filter((x) => x.n > 0)
}

export function openBranch(): void {
  if (S.branch) {
    if (S.branch.project !== S.project) toast(`你在别的项目里还开着分支「${S.branch.name}」：先合回或放弃它`)
    return
  }
  if (!isShared(S.project)) return
  snapshotAll()
  S.branch = { project: S.project, name: `${NAMES[ME]}-试改`, files: {}, asked: false, approved: false }
  toast(`开了分支「${S.branch.name}」：在这里改不影响正式版，改好了再合回去`)
}

/** 请人看一下: in the demo the other person (王磊, or 周珂 in the team's 题目) looks at it a moment later. */
export function askReview(): void {
  const b = S.branch
  if (!b || b.asked) return
  b.asked = true
  const who = NAMES[reviewerOf(b.project)]
  toast(`已请${who}看一下`)
  later(1800, () => {
    if (S.branch !== b) return
    b.approved = true
    toast(`${who}看过了分支「${b.name}」：可以合`)
  })
}

export const canMerge = () =>
  !!S.branch && branchChanges().length > 0 && (!S.protectMain[S.branch.project] || S.branch.approved)

/** 合回正式版: the branch's changes go into 正式版 as one 历史 entry, and the branch closes. */
export function mergeBranch(): void {
  const b = S.branch
  if (!b || !canMerge()) return
  const changed = branchChanges()
  const prevs: Record<string, string> = {}
  for (const { path } of changed) {
    prevs[path] = officialText(b.project, path)
    S.official[fkey(b.project, path)] = b.files[path]!
  }
  S.branch = null
  log(b.project, ME, `把分支「${b.name}」合回正式版：改了 ${changed.map((c) => c.path).join('、')}`, { prevs })
  toast(`已合回正式版：${changed.length} 个文件`)
}

export function dropBranch(): void {
  if (!S.branch) return
  const name = S.branch.name
  S.branch = null
  toast(`分支「${name}」已放弃，正式版没变`)
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

/** 运行: the file as it reads now (the branch's copy while on one), in my own environment; the output lands in the bottom panel and the terminal. */
export function runCode(p: ProjectKey, path: string): void {
  const k = fkey(p, path)
  const text = codeText(p, path)
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
      out: code ? codeText(p, f) : files.includes(f) ? '（文档，在项目文件里打开）' : `cat: ${f}: 没有这个文件`,
    })
    return
  }
  if (/^(make|\.\/|g\+\+|python|bash)/.test(c)) {
    list.push({ cmd: c, out: runOf(p, path, codeText(p, path)).out })
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
  // What was just typed becomes its 历史 entry first; the judged version is the file as it reads now.
  snapshot(p, path)
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

// ---- a file in the conversation: mentions, 问芝士 ----

/** Every file path of a project's 项目文件. */
export const pathsOf = (p: ProjectKey): string[] =>
  isClaim(p) ? PROJECT_FILES[p].map((f) => f.path) : p === 'research' ? FILES.map((f) => f.path) : []

/** A file mention as messages write it, `path`, `path:12` or `path:12-14`, if it names a file of the project. */
export function fileRef(p: ProjectKey, text: string): { path: string; start?: number; end?: number } | null {
  const m = /^(.+?)(?::(\d+)(?:-(\d+))?)?$/.exec(text.trim())
  if (!m || !pathsOf(p).includes(m[1]!)) return null
  const start = m[2] ? Number(m[2]) : undefined
  return { path: m[1]!, start, end: m[3] ? Number(m[3]) : start }
}

/** Open a file in 项目文件 at a line (from a mention in a channel): the editor brings it into view and marks it. */
export function openFileAt(path: string, start?: number, end?: number): void {
  openFile(path, { ftab: 'talk' })
  if (start) S.codeAt = { path, start, end: end ?? start, tick: (S.codeAt?.tick ?? 0) + 1 }
}

/** The messages in this project's channels that mention a file. */
export function mentionsOf(path: string): { chan: string; line: ChatLine }[] {
  return channelsOf(S.project).flatMap((c) =>
    (S.chat[c.id] ?? []).filter((l) => l.text.includes(path)).map((line) => ({ chan: c.id, line }))
  )
}

/** The channel a question about a file goes to: where it was last talked about, else the project's main one. */
function chanForFile(p: ProjectKey, path: string): string {
  if (isClaim(p)) return CLAIMED[p].chan
  return mentionsOf(path).at(-1)?.chan ?? 'c-exp'
}

/** 芝士's answer about a research file, read off the file as it is now. */
function answerAbout(path: string, ref: string, lines: string[]): string {
  const text = codeText('research', path)
  const quote = lines.length ? `\n\n\`\`\`\n${lines.join('\n')}\n\`\`\`` : ''
  if (path === 'eval/preprocess.py')
    return /CenterCrop\(\s*224\s*\)/.test(text)
      ? `\`${ref}\` 现在是 \`Resize(224, BICUBIC)\` + \`CenterCrop(224)\`，和论文、和《评测口径说明》都一致，输入正好 224×224。${quote}`
      : `\`${ref}\` 只做了 \`Resize(256)\`，没有 center crop，进模型的不是 224×224，模型里会再缩放一次。论文是 224 + BICUBIC + center crop，Flickr30K 差的那 1 个点多半在这里。${quote}`
  if (path === 'eval/retrieval.py')
    return text.includes('recall_at_k')
      ? `\`${ref}\` 现在一次出 R@1 / R@5 / R@10，\`recall_at_k\` 按相似度排序取前 k 个，看正确的那条在不在里面。${quote}`
      : `\`${ref}\` 现在只算 R@1（\`recall_at_1\`）。要 R@5 / R@10 的话，把它换成按 k 取前几名的写法。${quote}`
  return `看了 \`${ref}\`：${quote || '这个文件不长，'}有具体想改的地方可以直接改，正式版就是它。`
}

/** 问芝士 from an open file: the question goes to the channel with the file (and the selected lines) as context;
 *  芝士 answers under it there. Both show in the file's 对话. */
export function askAboutFile(): void {
  const q = S.asking.trim()
  const p = S.project
  const path = S.file
  if (!q) return
  const sel = S.codeSel?.path === path ? S.codeSel : null
  const ref = sel ? `${path}:${sel.start}${sel.end > sel.start ? `-${sel.end}` : ''}` : path
  const lines = sel
    ? codeText(p, path)
        .split('\n')
        .slice(sel.start - 1, sel.end)
    : []
  const chan = chanForFile(p, path)
  const id = nextId('f')
  S.asking = ''
  ;(S.chat[chan] ??= []).push({ id, who: ME, at: new Date().toISOString(), text: `<@cheese> \`${ref}\` ${q}` })
  later(1300, () =>
    S.chat[chan]!.push({
      id: nextId('c'),
      who: 'cheese',
      at: new Date().toISOString(),
      text: isClaim(p) ? `关于 \`${ref}\`：${hintFor(q)}` : answerAbout(path, ref, lines),
      replyTo: id,
    })
  )
  toast(`问题发在了 # ${channelsOf(p).find((c) => c.id === chan)?.title ?? ''}，芝士的回答也在那里`)
}

// ---- 领取: a 题目 becomes a project owned by a team ----

/** 领取 on a 题目 row: one that a team may claim first asks 「自己领」 or 「和团队一起领」. */
export function claim(topicId: string): void {
  const t = topicById(topicId)
  if (!t?.project) {
    toast('演示里能领取的是 Data Lab、第 3 周题单和 Shell Lab 大作业')
    return
  }
  if (S.claimed.includes(t.project)) {
    switchProject(t.project)
    return
  }
  if (t.mode === 'solo') claimAs(topicId, ME)
  else S.claimAsk = topicId
}

/** The claim itself: the project goes to the team (`linxiao` is 林晓's own team), one per team per 题目. */
export function claimAs(topicId: string, team: string): void {
  const t = topicById(topicId)
  if (!t?.project) return
  const p = t.project
  S.claimAsk = null
  if (!S.claimed.includes(p)) {
    S.claimed.push(p)
    const files = PROJECT_FILES[p]
    for (const f of files) if (f.kind === 'code') S.official[fkey(p, f.path)] = f.starter!
    S.chat[CLAIMED[p].chan] = welcome(p, monthDayTime(Date.parse(t.due)))
    S.chanView[CLAIMED[p].chan] = 'talk'
    S.collapsed[p] = p === 'week3' ? SET.map((x) => x.id) : []
    S.fileOf[p] = files[0]!.path
    const owner = teamOf(team)
    const made =
      p === 'datalab' ? 'README.md 和 bits.c' : p === 'week3' ? '题单.md 和 5 道题的文件' : 'README.md 和 tsh.c'
    S.history[p] = [
      {
        who: ME,
        what: owner.personal
          ? `领取了「${t.title}」：新建了 ${made}`
          : `代表「${owner.name}」领取了「${t.title}」：新建了 ${made}`,
        at: new Date().toISOString(),
        fixed: true,
      },
    ]
    toast(
      owner.personal
        ? `领取了「${t.title}」：项目「${CLAIMED[p].name}」建在你自己的团队下`
        : `「${owner.name}」领取了「${t.title}」：${owner.members.length} 个人共用项目「${CLAIMED[p].name}」`
    )
  }
  switchProject(p)
}

/** Where a 题目 stands for me: 未领取, 进行中 · 项目名, or 已提交 with the latest score. */
export function standing(topicId: string): { text: string; tone: 'new' | 'busy' | 'done' } {
  const p = topicById(topicId)?.project
  if (!p || !S.claimed.includes(p)) return { text: '未领取', tone: 'new' }
  const name = CLAIMED[p].name
  if (p === 'week3') {
    const tried = SET.filter((x) => latestOf(p, x.id))
    const passed = tried.filter((x) => {
      const l = latestOf(p, x.id)!
      return l.score === l.total
    })
    return tried.length
      ? { text: `已提交 · 通过 ${passed.length}/${SET.length}`, tone: 'done' }
      : { text: `进行中 · ${name}`, tone: 'busy' }
  }
  const last = latestOf(p, p)
  return last
    ? { text: `已提交 · ${last.score}/${last.total}`, tone: 'done' }
    : { text: `进行中 · ${name}`, tone: 'busy' }
}

let seq = 0
const nextId = (p: string) => `${p}${Date.now().toString(36)}${seq++}`

/** The channel composer: an @芝士 opens a 讨论 under the message; anything else is just said. */
export function send(): void {
  const text = S.typing.trim()
  if (!text) return
  S.typing = ''
  const at = new Date().toISOString()
  // In a project claimed from a 题目 芝士 only gives hints, in the channel: no 一件事 is opened there.
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
  const prevs: Record<string, string> = {}
  for (const [path, c] of Object.entries(CODE)) {
    if (!c.after) continue
    prevs[path] = officialText('research', path)
    S.official[fkey('research', path)] = c.after.join('\n')
  }
  log('research', ME, '采纳了任务「统一评测脚本」：改了 2 个文件，新增 results/eval_results.md', { prevs })
  toast('已采纳：进了项目文件的正式版')
}

/** 采纳并在编辑器里打开: accept, then open the first changed file on 正式版, where people edit it directly. */
export function acceptAndOpen(): void {
  accept()
  openFile('eval/preprocess.py', { ftab: 'history' })
}

/** Tell 芝士 what to change in its copy: the task's own conversation, with an @ ready. */
export function tellCheese(): void {
  S.rtab = 'chat'
  if (!S.thingTyping.includes('<@cheese>')) S.thingTyping = `<@cheese> ${S.thingTyping}`
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
  for (const [path, prev] of Object.entries(h.prevs ?? {})) S.official[fkey(S.project, path)] = prev
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
      out.push({ id: t.id, title: `${t.title} ${chip}`, sub: '还没领取', chip, open: () => showTopic(t.id) })
      continue
    }
    if (p === 'week3') {
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
      continue
    }
    const last = latestOf(p, p)
    if (last && last.score === last.total) continue
    const file = PROJECT_FILES[p].find((f) => f.unit === p)!.path
    out.push({
      id: t.id,
      title: `${t.title} ${chip}`,
      sub: last ? `最近一次 ${last.score}/${last.total}` : '还没提交',
      chip,
      open: () => {
        switchProject(p)
        openFile(file)
      },
    })
  }
  return out
}
