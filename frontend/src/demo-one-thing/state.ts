// Everything the demo screen shows is read from this one reactive object, and
// every click (a person's or the scripted cursor's) goes through the actions
// below, so playing, stepping and clicking by hand always agree.
import type { ReactionAgg } from '@/cx_types'
import type { RunInput, RunOut, Submission, Verdict } from './course'

import { reactive } from 'vue'

import {
  dueChip,
  hintFor,
  PROJECT_FILES,
  reviewShell,
  SET,
  submitItems,
  topicById,
  TOPICS,
  UNITS,
  versionOf,
  welcome,
  WORK_TITLE,
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
  isShared,
  ME,
  minsAgo,
  MINUTES_DOC,
  NAMES,
  PLAN,
  type ProjectKey,
  projectOfChan,
  projectTeam,
  RESULTS_MD,
  teamOf,
} from './data'
import { T4_STEPS } from './site'

import { monthDayTime } from '@/lib/dateText'

export type Status = 'talk' | 'run' | 'wait' | 'done'
/** inbox: 待办 on 首页, or 需要我的 inside a project (the same cross-project list). space: the
 *  course 空间's pages. team: a team's 项目 list on 首页. The others are a project's. */
export type Center = 'inbox' | 'chan' | 'files' | 'settings' | 'space' | 'team'
export type SpacePage = 'tasks' | 'announce' | 'library'
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

/** A quiet line in a 一件事's conversation: 开工, 接手, 采纳. */
export interface MarkLine {
  id: string
  mark: string
  at: string
  ok?: boolean
}

/** One 运行 in a 一件事's 工作台: who ran what, on which file of the copy, and what it printed. A 题单 sample
 *  run also says what was expected and whether it matched; a graded file's run counts what passed. */
export interface Run extends RunOut {
  id: string
  who: string
  at: string
  path?: string
  running?: boolean
  unit?: string
  ok?: number
  total?: number
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
  /** Who does it once started: 芝士 (unset), or a person (「改这个文件」, a course's 一件事). */
  doer?: string
  /** A team doing it together (a team 题目's 一件事): each of them can work in its 工作台. */
  team?: string[]
  /** Opened by 领取 in a project claimed from a 题目: 芝士 only gives hints in its 对话 and never works on it. */
  course?: boolean
  /** What 开工 hands over, as the 开工 sheet says it (演示一's task has its own). */
  deliver?: string
  /** Its 工作台's files: its own copy of each file it touches (what 正式版 read when the copy was taken, and
   *  the copy's text now). Only 采纳, in the 一件事, puts it into 正式版. */
  copy?: Record<string, { base: string; text: string }>
  /** When the copy was taken from 正式版 (开工, 改这个文件, 领取): 正式版 changed after it is settled at 采纳. */
  copyAt?: string
  /** Files it makes that are not code (a result table): path → what it reads, shown in its 工作台 and
   *  named in the 历史 entry its 采纳 makes. */
  adds?: Record<string, string>
  /** Every 运行 made in its 工作台, by 芝士 or a person, oldest first. */
  runs?: Run[]
  /** A person who took over 芝士's work (接手): 芝士 waits until it is handed back. */
  takenBy?: string | null
  /** Everyone who has worked on it, in the order they started: its 采纳 says who did it. */
  by?: string[]
  /** Where 正式版 and the copy both changed the same lines: which side the adopter picked, per file and block. */
  picks?: Record<string, Record<number, Side>>
}

/** A side of a block both 正式版 and a 一件事's copy changed: 正式版 as it reads now, or the 一件事's. */
export type Side = 'ours' | 'theirs'

export interface HistoryEntry {
  who: string
  what: string
  at: string
  fresh?: boolean
  /** A change to code files (an accepted 一件事): path → text before, which 撤回 puts back. */
  prevs?: Record<string, string>
  /** Nothing to take back (领取 itself). */
  fixed?: boolean
  /** How it got into 正式版 when it was a 一件事's 采纳: the 一件事 (linked when there is one), and who did it. */
  via?: { thing?: string; title: string; by: string }
  /** The document or file it is about when that is not in `prevs` (a document): for 这个文件's 历史. */
  about?: string
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
    /** The 题目 open in the space (its detail and 提交记录), from a row, a project's origin card or a deadline. */
    spaceTopic: null as string | null,
    /** 全部题目's 「上次查看后新发布」 count: cleared once the list has been seen and left. */
    spaceNew: 3,
    /** 领取 asking 「自己领」 or 「和团队一起领」 for this 题目. */
    claimAsk: null as string | null,
    /** The 提交 dialog: the 题目, and what is graded (the 题目 itself, or one problem of the 题单). */
    submitAsk: null as null | { topic: string; unit: string },
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
    /** The 交给芝士 sheet in the open 一件事's header, and the note going with it. */
    hand: false,
    handNote: '',
    morph: false,
    typing: '',
    thingTyping: '',
    replyTo: null as string | null,
    thingReplyTo: null as string | null,
    live: 0,
    file: MINUTES_DOC,
    suggAccepted: false,
    suggRejected: false,
    typed: '',
    toast: '',
    toastTick: 0,
    settingsSection: 'project',
    settingsNotify: 'mentions',
    settingsRuntime: 'a100',
    /** Every project's code files as 正式版 reads now, keyed by fkey. 项目文件 shows them read-only; only a
     *  一件事's 采纳 changes them. */
    official: Object.fromEntries(
      Object.entries(CODE).map(([path, c]) => [fkey('research', path), c.before.join('\n')])
    ),
    /** Lines to bring into view and mark in 项目文件's code view (a file mention clicked in a channel). */
    codeAt: null as { path: string; start: number; end: number; tick: number } | null,
    /** The open 一件事's 工作台 is showing (where depends on 方案「工作台放哪」). */
    bench: false,
    /** The file each 一件事's 工作台 has open, once chosen there. */
    benchFile: {} as Record<string, string>,
    /** Where 芝士 is typing now: the 一件事, the file of its copy, the line. Cleared when it stops. */
    cheeseAt: null as null | { thing: string; path: string; line: number },
    /** What 运行 feeds a 题单 problem in a 工作台, per 一件事 and file: 样例 1, 样例 2, or input typed there. */
    runIn: {} as Record<string, { pick: 0 | 1 | 'custom'; text: string }>,
    /** Every submission to each 题目, newest first: recorded on the 题目, not in the project. */
    subs: { datalab: [], week3: [], shelllab: [] } as Record<string, Submission[]>,
    /** Submissions whose result (the judge's verdict, the teacher's review) I have looked at. */
    seen: {} as Record<string, boolean>,
    /** The 题目 card under a claimed project's header, opened or closed by hand, per 题目; unset, it opens by
     *  itself when it matters. */
    cardOpen: {} as Record<string, boolean>,
    /** A submission whose pinned version is open (read-only) in 项目文件's code view. */
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
        { who: 'wanglei', what: '直接改了《组会纪要 11-07》', at: minsAgo(9), about: MINUTES_DOC },
        {
          who: 'wanglei',
          what: '采纳了「batch_size 改回 256」（王磊做的）：改了 configs/clip_vit_b32.yaml',
          at: minsAgo(26),
          via: { title: 'batch_size 改回 256', by: '王磊做的' },
          prevs: {
            'configs/clip_vit_b32.yaml': CODE['configs/clip_vit_b32.yaml']!.before.join('\n').replace(
              'batch_size: 256',
              'batch_size: 128'
            ),
          },
        },
        {
          who: 'chensiyuan',
          what: '采纳了「补长描述截断实验」（芝士做的）：改了 2 个文件',
          at: dayAt(1, 17, 42),
          via: { title: '补长描述截断实验', by: '芝士做的' },
        },
        {
          who: 'zhaoyutong',
          what: '直接上传了 report/中期报告.docx',
          at: dayAt(1, 11, 5),
          about: 'report/中期报告.docx',
        },
        {
          who: 'wanglei',
          what: '采纳了「BLIP 精读笔记」（芝士做的）：新增 docs/papers/BLIP-精读.md',
          at: dayAt(3, 15, 20),
          via: { title: 'BLIP 精读笔记', by: '芝士做的' },
          about: 'docs/papers/BLIP-精读.md',
        },
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
        adds: {
          'results/caption_lengths.md':
            '| token 数 | 训练集 | 5K 测试集 |\n| --- | --- | --- |\n| ≤ 20 | 71.4% | 72.0% |\n| 21–77 | 22.3% | 21.9% |\n| > 77 | 6.3% | 6.1% |',
        },
        runs: [
          {
            id: 't1r1',
            who: 'cheese',
            at: minsAgo(50),
            cmd: 'python scripts/caption_lengths.py --split train2017 --split 5k',
            out: 'train2017: 591753 captions  >77: 37281 (6.3%)\n5k:        25010 captions  >77:  1526 (6.1%)',
          },
        ],
        copy: {},
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
        copy: {},
        runs: [
          {
            id: 't2r1',
            who: 'cheese',
            at: minsAgo(30),
            cmd: 'python -m eval.retrieval --model blip_base_14M --data flickr30k --split 1k',
            out: 'encoding 1000 images, 5000 captions … done (41s)\nR@1 81.9  R@5 95.4  R@10 97.8',
          },
          {
            id: 't2r2',
            who: 'cheese',
            at: minsAgo(6),
            cmd: 'python -m eval.retrieval --model blip_base_129M --data flickr30k --split 1k',
            out: 'loading checkpoint blip_base_129M … 38%',
            running: true,
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
        copy: {},
        adds: {
          'report/组会-11-07.pdf':
            '**组会 11-07**，7 页：\n\n1. 本周进展\n2. CLIP 零样本检索结果\n3. 和论文的差距\n4. 长描述截断实验设计\n5. 下周计划',
        },
        runs: [
          {
            id: 't3r1',
            who: 'cheese',
            at: dayAt(1, 16, 38),
            cmd: 'marp slides.md -o report/组会-11-07.pdf',
            out: '[  INFO ] slides.md => report/组会-11-07.pdf (7 pages)',
          },
        ],
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
/** Timers still waiting: a tour fast-forwarding to a step runs them at once (settle). */
const pending = new Set<() => void>()
const later = (ms: number, fn: () => void) => {
  const my = gen
  const run = () => {
    if (!pending.delete(run)) return
    if (my === gen) fn()
  }
  pending.add(run)
  setTimeout(run, ms)
}

/** What timers were going to do, done now in order, until `until` holds (a tour fast-forwarding to the middle
 *  of 芝士's work); without it, until nothing is left. */
export function settleUntil(until: () => boolean): void {
  while (pending.size && !until()) [...pending][0]!()
}

/** Everything a timer was still going to do, done now, in the order it was asked for (and what those then ask for). */
export function settle(): void {
  while (pending.size) for (const run of [...pending]) run()
}

export function reset(): void {
  const keep = S.showNotes
  gen++
  pending.clear()
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
    S.bench = false
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
  // What was half-typed belongs to the channel it was typed in, not to the other project.
  S.typing = ''
  S.replyTo = null
  S.viewSub = null
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

/** The course 空间, from its rail icon, 首页's list, a project's origin card or a deadline: one of its pages. */
export function openSpace(page: SpacePage = 'tasks', extra: Partial<typeof S> = {}): void {
  leaveProject()
  S.area = 'home'
  if (page !== 'tasks' || S.spacePage !== 'tasks') S.claimAsk = null
  go('space', { spacePage: page, spaceCat: null, spaceTopic: null, ...extra })
}

/** Open one 题目 in the space: what it asks, how it is graded, and its 提交记录. */
export function showTopic(id: string): void {
  openSpace('tasks', { spaceTopic: id })
}

/** Unread messages in one space's channels (the rail's count). */
export const unreadIn = (p: ProjectKey) => channelsOf(p).reduce((n, c) => n + (S.unread[c.id] ?? 0), 0)

/** Open a 一件事 in its channel, on one of its tabs (对话 unless asked; 改动 from a file it changes). */
export function openThing(id: string, rtab = 'chat'): void {
  const t = thingById(id)
  if (!t) return
  if (projectOfChan(t.chan) !== S.project) switchProject(projectOfChan(t.chan))
  go('chan', { chan: t.chan, thing: id, rtab, bench: false, hand: false })
}

/** A 一件事 that has a 工作台: started (演示一's task once 开工), with files or runs of its own. */
export const hasBench = (t: Thing) => t.status !== 'talk' && (!!t.copy || !!t.runs || t.id === 't4')

/** Open a 一件事 on its 工作台, with one of its files open there (「改这个文件」, 「接着做」, a 题单 row). */
export function openBench(id: string, path?: string): void {
  const t = thingById(id)
  if (!t) return
  openThing(id)
  if (path) S.benchFile[id] = path
  S.bench = hasBench(t)
}

export function closeThing(): void {
  S.thing = null
  S.focus = false
  S.bench = false
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

// ---- code: 正式版, and the 一件事 that change it ----
//
// 正式版 is what 项目文件 shows, and its code is read-only there. Every change
// to code is a 一件事, whoever does it: 芝士 (asked in a channel, a 讨论,
// then 开工), or a person (「改这个文件」 opens one I do, straight into its
// 工作台; 领取 opens the course's one). Each 一件事 has a 工作台: its own copy
// of the files it touches, every 运行 made in it and what it printed. Whoever
// is doing it changes the copy: 芝士 live while people watch, a person by
// typing. 接手 and 交给芝士 move the doing between them, in the same 工作台.
// Done (改好了, or 芝士 finishing), its 改动 is 采纳ed in the 一件事 by a
// person, the one way into 正式版 (acceptCopy); 历史 says which 一件事 and who
// did it.

/** A code file as 正式版 reads now, in any project. */
export function officialText(p: ProjectKey, path: string): string {
  return S.official[fkey(p, path)] ?? ''
}

/** Who looks at a person's 一件事 in the demo: someone else in the project. */
const reviewerOf = (p: ProjectKey) => (p === 'research' ? 'wanglei' : 'zhouke')

/** The judged unit a file belongs to (Data Lab's bits.c, a 题单 problem's main.cpp), if any. */
export const unitOf = (p: ProjectKey, path: string) =>
  isClaim(p) ? PROJECT_FILES[p].find((f) => f.path === path)?.unit : undefined

function log(p: ProjectKey, who: string, what: string, extra: Partial<HistoryEntry> = {}): void {
  const list = S.history[p]
  for (const h of list) h.fresh = false
  list.unshift({ who, what, at: new Date().toISOString(), fresh: true, ...extra })
}

/** Who is working on a started 一件事 right now: whoever took it over, else its doer; nobody once it is done. */
export function doingNow(t: Thing): string | null {
  if (t.status !== 'run') return null
  return t.takenBy ?? t.doer ?? 'cheese'
}

/** I can type and 运行 in this 一件事's 工作台 now: I am the one doing it, or one of the team doing it. */
export function canWork(t: Thing): boolean {
  const who = doingNow(t)
  return !!who && who !== 'cheese' && (who === ME || !!t.team?.includes(ME))
}

/** 接手 is offered: 芝士 is doing a 一件事 with files of its own and nobody has taken it over (演示一's scripted
 *  task, and a course's, excepted). */
export const canTake = (t: Thing) =>
  t.id !== 't4' && !t.course && doingNow(t) === 'cheese' && Object.keys(t.copy ?? {}).length > 0

/** Typing in a 工作台: it changes the 一件事's copy, never 正式版. */
export function typeCopy(id: string, path: string, text: string): void {
  const t = thingById(id)
  const c = t?.copy?.[path]
  if (!t || !c || !canWork(t)) return
  c.text = text
}

/** The files a 工作台 lists: the copy's, then what it made. */
export const benchFiles = (t: Thing) => [...Object.keys(t.copy ?? {}), ...Object.keys(t.adds ?? {})]

/** The file a 工作台 shows: the one 芝士 is typing in while it works, else the one picked there, else the first. */
export function benchFileOf(t: Thing): string | null {
  const files = benchFiles(t)
  const live = S.cheeseAt?.thing === t.id && doingNow(t) === 'cheese' ? S.cheeseAt.path : null
  const picked = S.benchFile[t.id]
  return live ?? (picked && files.includes(picked) ? picked : files[0] ?? null)
}

/** Everyone who worked on it, kept in order: who its 采纳 credits. */
function worked(t: Thing, who: string): void {
  t.by ??= []
  if (!t.by.includes(who)) t.by.push(who)
}

// ---- 运行, in a 工作台 ----

/** The research project's files, run on the text as it now reads: a short, plausible run. */
function researchRun(path: string, text: string): RunOut {
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
    // argsort without descending=True ranks the least similar first: the numbers come out near zero.
    const wrong = all && !/descending\s*=\s*True/.test(text)
    return {
      cmd: 'python -m eval.retrieval --data flickr30k --split 1k --limit 100',
      out: `encoding 100 images, 500 captions … done (3.8s)\n${
        wrong ? 'R@1 0.0  R@5 1.0  R@10 2.0' : all ? 'R@1 79.0  R@5 94.0  R@10 97.0' : 'R@1 78.0'
      }   （只抽了前 100 张，快速检查）`,
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

/** A run of one file of a 工作台's copy: a graded file runs its own tests (a 题单 problem on the input picked
 *  for it) and counts what passed, for the 题目 card; a research file runs as researchRun says. */
function runOf(t: Thing, path: string, text: string): Omit<Run, 'id' | 'who' | 'at'> {
  const p = projectOfChan(t.chan)
  const unit = unitOf(p, path)
  if (!unit) return { path, ...researchRun(path, text) }
  const v = UNITS[unit]!.judge(text)
  const sel = S.runIn[`${t.id}:${path}`]
  const input: RunInput | undefined = !sel
    ? undefined
    : sel.pick === 'custom'
      ? { custom: sel.text }
      : { sample: sel.pick }
  return { path, unit, ok: v.rows.filter((r) => r.ok).length, total: v.rows.length, ...UNITS[unit]!.run(text, input) }
}

/** 运行 in a 工作台, by the person doing it: the file as the copy reads now. The output stays in the 一件事. */
export function runInBench(id: string, path: string): void {
  const t = thingById(id)
  const c = t?.copy?.[path]
  if (!t || !c || !canWork(t)) return
  const text = c.text
  t.runs ??= []
  const runs = t.runs
  runs.push({ id: nextId('r'), who: ME, at: new Date().toISOString(), path, cmd: '', out: '', running: true })
  // The reactive copy: what is set on it later reaches every view.
  const run = runs.at(-1)!
  later(800, () => Object.assign(run, runOf(t, path, text), { running: false }))
}

/** The 一件事 领取 opened in a claimed project. */
export const workOf = (p: ProjectKey) => (isClaim(p) ? thingById(`w-${p}`) : null)

/** The latest finished run of a graded unit in that 一件事: how much passed (the 题目 card's first line). */
export function lastRun(p: ProjectKey, unit: string): Run | null {
  return (
    workOf(p)
      ?.runs?.filter((r) => r.unit === unit && !r.running)
      .at(-1) ?? null
  )
}

// ---- 芝士 working in a 工作台 ----

/** One step of 芝士's work: after `ms`, unless the 一件事 has stopped running or a person has taken it over
 *  (`always`: a step that only finishes what was already under way, such as a run's output arriving). */
interface Act {
  ms: number
  run: (t: Thing) => void
  always?: boolean
}

/** 芝士's steps, one after another. A step that finds the 一件事 taken over (接手) ends the chain there. */
function play(id: string, acts: Act[]): void {
  const [a, ...rest] = acts
  if (!a) return
  later(a.ms, () => {
    const t = thingById(id)
    if (!t) return
    if (doingNow(t) !== 'cheese' && !a.always) {
      if (S.cheeseAt?.thing === id) S.cheeseAt = null
      return
    }
    a.run(t)
    play(id, rest)
  })
}

/** 芝士 typing `to` over `from` in one file of the copy, a line at a time: in each changed block the old lines
 *  go, then the new ones come in one by one, with its caret on the line it is on. */
function typeActs(path: string, from: string, to: string, ms = 300): Act[] {
  const cur = from.split('\n')
  const frames: { text: string; line: number }[] = []
  let shift = 0
  for (const h of hunksOf(from.split('\n'), to.split('\n'))) {
    const at = h.s + shift
    if (h.e > h.s) {
      cur.splice(at, h.e - h.s)
      frames.push({ text: cur.join('\n'), line: at + 1 })
    }
    h.lines.forEach((l, i) => {
      cur.splice(at + i, 0, l)
      frames.push({ text: cur.join('\n'), line: at + i + 1 })
    })
    shift += h.lines.length - (h.e - h.s)
  }
  return frames.map((f) => ({
    ms,
    run: (t) => {
      t.copy![path]!.text = f.text
      S.cheeseAt = { thing: t.id, path, line: f.line }
    },
  }))
}

/** 芝士 runs a file of its copy (or the command given): the command shows in the 工作台 at once, its output
 *  a moment later. */
function runActs(path: string, fixed?: RunOut): Act[] {
  let run: Run | null = null
  return [
    {
      ms: 500,
      run: (t) => {
        t.runs ??= []
        const runs = t.runs
        const r = fixed ?? runOf(t, path, t.copy![path]!.text)
        runs.push({ ...r, id: nextId('r'), who: 'cheese', at: new Date().toISOString(), path, running: true })
        run = runs.at(-1)!
        S.cheeseAt = null
      },
    },
    { ms: 900, always: true, run: () => run && (run.running = false) },
  ]
}

/** 芝士 says something in the 一件事's 对话. */
const sayAct = (ms: number, text: string, last = text): Act => ({
  ms,
  run: (t) => {
    say(t, { id: nextId('c'), who: 'cheese', at: new Date().toISOString(), text })
    t.replies++
    t.last = `芝士：${last}`
  },
})

const doneAct: Act = { ms: 600, run: (t) => finishCopy(t.id) }

// ---- a file in the conversation: mentions, and 讨论 about a file ----

/** Every file path of a project's 项目文件. */
export const pathsOf = (p: ProjectKey): string[] =>
  isClaim(p) ? PROJECT_FILES[p].map((f) => f.path) : p === 'research' ? FILES.map((f) => f.path) : []

/** A file and, if any, the lines of it a message is about. */
export interface FileAt {
  path: string
  start?: number
  end?: number
}

/** A file mention as messages write it, `path`, `path:12` or `path:12-14`, if it names a file of the project. */
export function fileRef(p: ProjectKey, text: string): FileAt | null {
  const m = /^(.+?)(?::(\d+)(?:-(\d+))?)?$/.exec(text.trim())
  if (!m || !pathsOf(p).includes(m[1]!)) return null
  const start = m[2] ? Number(m[2]) : undefined
  return { path: m[1]!, start, end: m[3] ? Number(m[3]) : start }
}

/** A file mention written back out: `path`, `path:12` or `path:12-14`. */
const refText = (r: FileAt) => (r.start ? `${r.path}:${r.start}${r.end && r.end > r.start ? `-${r.end}` : ''}` : r.path)

/** Open a file in 项目文件 at a line (from a mention in a channel): the code view brings it into view and marks it. */
export function openFileAt(path: string, start?: number, end?: number): void {
  openFile(path)
  if (start) S.codeAt = { path, start, end: end ?? start, tick: (S.codeAt?.tick ?? 0) + 1 }
}

/** The messages in this project's channels that mention a file. */
function mentionsOf(path: string): { chan: string; line: ChatLine }[] {
  return channelsOf(S.project).flatMap((c) =>
    (S.chat[c.id] ?? []).filter((l) => l.text.includes(path)).map((line) => ({ chan: c.id, line }))
  )
}

/** The channel a file's 一件事 goes to: where the file was last talked about, else the project's main one. */
function chanForFile(p: ProjectKey, path: string): string {
  if (isClaim(p)) return CLAIMED[p].chan
  return mentionsOf(path).at(-1)?.chan ?? 'c-exp'
}

/** 芝士's answer about a research file, read off the file as it is now. */
function answerAbout(path: string, ref: string, lines: string[]): string {
  const text = officialText('research', path)
  const quote = lines.length ? `\n\n\`\`\`\n${lines.join('\n')}\n\`\`\`` : ''
  if (path === 'eval/preprocess.py')
    return /CenterCrop\(\s*224\s*\)/.test(text)
      ? `\`${ref}\` 现在是 \`Resize(224)\` + \`CenterCrop(224)\`，和论文、和《评测口径说明》都一致，输入正好 224×224。${quote}`
      : `\`${ref}\` 只做了 \`Resize(256)\`，没有 center crop，进模型的不是 224×224，模型里会再缩放一次。论文是 224 + BICUBIC + center crop，Flickr30K 差的那 1 个点多半在这里。${quote}`
  if (path === 'eval/retrieval.py')
    return text.includes('recall_at_k')
      ? `\`${ref}\` 现在一次出 R@1 / R@5 / R@10，\`recall_at_k\` 按相似度排序取前 k 个，看正确的那条在不在里面。${quote}`
      : `\`${ref}\` 现在只算 R@1（\`recall_at_1\`）。要 R@5 / R@10，得换成按 k 取前几名的写法。${quote}`
  return `看了 \`${ref}\`。${quote}`
}

/** What 芝士 would make of a research code file asked to change it: the demo's one prepared change per file
 *  (the same text 演示一's task brings), if 正式版 does not read so already. */
function proposalFor(p: ProjectKey, path: string): string | null {
  const after = p === 'research' ? CODE[path]?.after?.join('\n') : undefined
  return after && after !== officialText(p, path) ? after : null
}

/** What 芝士 says its prepared change does, per file. */
const CHANGE_NOTE: Record<string, string> = {
  'eval/retrieval.py': '`evaluate` 一次算出 R@1 / R@5 / R@10，新加了 `recall_at_k`',
  'eval/preprocess.py': '预处理改成 `Resize(224, BICUBIC)` + `CenterCrop(224)`，和论文一致',
}

/** An @芝士 in the research project that names a file: a 讨论 under the message, like any other. 芝士 answers
 *  about the file; where it has a change to offer, the 一件事 can be started (开工) and 芝士 then works on its
 *  own copy of the file, in the 一件事's 工作台. */
function discussCode(chan: string, text: string, ref: FileAt): void {
  const p: ProjectKey = 'research'
  const at = new Date().toISOString()
  const id = nextId('tc')
  const proposal = proposalFor(p, ref.path)
  const lines = ref.start
    ? officialText(p, ref.path)
        .split('\n')
        .slice(ref.start - 1, ref.end ?? ref.start)
    : []
  // 「把 `eval/retrieval.py` 改成…」 reads 「retrieval.py：改成…」 as a title.
  const ask = text
    .replace(/<@cheese>\s*/, '')
    .replace(/(把\s*)?`[^`]*`\s*/, '')
    .trim()
  S.things.unshift({
    id,
    title: `${ref.path.split('/').pop()}：${ask.slice(0, 24) || '问芝士'}`,
    chan,
    owner: ME,
    doer: 'cheese',
    status: 'talk',
    replies: 1,
    last: '芝士：我先看看这个文件',
    at,
    talk: [{ id: `${id}a`, who: ME, at, text }],
    copy: proposal ? { [ref.path]: { base: officialText(p, ref.path), text: officialText(p, ref.path) } } : undefined,
    deliver: proposal ? `改好的 ${ref.path}` : undefined,
  })
  S.chat[chan]!.push({ id: `${id}m`, who: ME, at, text, thing: id })
  toast('在这条消息下开了一个讨论')
  later(1300, () => {
    const t = thingById(id)
    if (!t || t.status !== 'talk') return
    const offer = proposal
      ? `\n\n我可以这样改：${CHANGE_NOTE[ref.path] ?? `改 \`${ref.path}\``}。要做就点「开工」：我在这件事的工作台里改，正式版不动；改到哪、跑出什么，打开工作台就看得到。`
      : ''
    t.talk!.push({
      id: `${id}b`,
      who: 'cheese',
      at: new Date().toISOString(),
      text: answerAbout(ref.path, refText(ref), lines) + offer,
    })
    t.replies++
    t.last = proposal ? '芝士：可以改成一次出几个数，要做就开工' : '芝士：看了这个文件'
  })
}

/** 开工 on a 讨论 about code: 芝士 takes its copy of the file from 正式版 as it reads now and works on it in
 *  the 工作台: it types its change, runs it, and when the numbers are off says so and fixes it (a person can
 *  接手 at any point), then says it is done. */
function startCopy(t: Thing): void {
  const p = projectOfChan(t.chan)
  const at = new Date().toISOString()
  const path = Object.keys(t.copy!)[0]!
  const base = officialText(p, path)
  t.copy![path] = { base, text: base }
  t.copyAt = at
  t.status = 'run'
  t.at = at
  t.runs ??= []
  worked(t, 'cheese')
  t.last = '芝士：开始改了，在工作台里'
  t.talk!.push(
    { id: `${t.id}k`, mark: `${NAMES[ME]} 开工 · 交给芝士`, at },
    { id: `${t.id}s`, who: 'cheese', at, text: '开始改了。我在这件事的工作台里改，正式版不动。' }
  )
  t.replies++
  S.morph = true
  toast('开工了，交给芝士')
  setTimeout(() => (S.morph = false), 1200)
  const goal = proposalFor(p, path) ?? base
  // Its first try at retrieval.py sorts the wrong way round; the run shows it, and it fixes it (or whoever took over does).
  const draft = goal.replace(', descending=True', '')
  play(t.id, [
    { ms: 1200, run: () => undefined },
    ...typeActs(path, base, draft),
    ...runActs(path),
    ...(draft === goal
      ? []
      : [
          sayAct(700, 'R@1 只有 0.0，排序方向反了：argsort 默认从小到大。我改一下再跑。', '排序方向反了，我改一下'),
          ...typeActs(path, draft, goal, 400).map((a, i) => (i ? a : { ...a, ms: 4200 })),
          ...runActs(path),
        ]),
    doneAct,
  ])
}

/** 芝士 is done with its copy: the change waits for 采纳, in the 一件事. */
function finishCopy(id: string): void {
  const t = thingById(id)
  if (!t?.copy || doingNow(t) !== 'cheese' || t.id === 't4') return
  const path = Object.keys(t.copy)[0]!
  t.status = 'wait'
  t.at = new Date().toISOString()
  t.last = '芝士：改好了，等你采纳'
  t.replies++
  if (S.cheeseAt?.thing === id) S.cheeseAt = null
  say(t, {
    id: `${id}c`,
    who: 'cheese',
    at: t.at,
    text: `改好了：${CHANGE_NOTE[path] ?? `改了 \`${path}\``}，在工作台里跑过。和正式版的差别在「改动」里，你采纳后才进正式版。`,
  })
}

/** 「改这个文件」 on a code file in 项目文件: a 一件事 I do, in the file's channel like any other, opened straight
 *  into its 工作台 with the file open. A 一件事 I am already doing on this file is opened instead of a second one. */
export function editFile(p: ProjectKey, path: string): void {
  const open = thingsOnFile(p, path).find((t) => canWork(t))
  if (open) return openBench(open.id, path)
  const chan = chanForFile(p, path)
  const at = new Date().toISOString()
  const id = nextId('tp')
  const title = `改 ${path.split('/').pop()}`
  const base = officialText(p, path)
  S.things.unshift({
    id,
    title,
    chan,
    owner: ME,
    doer: ME,
    status: 'run',
    replies: 0,
    last: `${NAMES[ME]}在工作台里改`,
    at,
    talk: [{ id: `${id}k`, mark: `${NAMES[ME]} 开了这件事 · 自己做`, at }],
    copy: { [path]: { base, text: base } },
    copyAt: at,
    runs: [],
    by: [ME],
  })
  ;(S.chat[chan] ??= []).push({ id: `${id}m`, who: ME, at, text: `改 \`${path}\``, thing: id })
  openBench(id, path)
  toast(`开了一件事「${title}」：在它的工作台里改，正式版不变`)
}

/** 改好了, on a 一件事 a person is doing: it waits for 采纳 in the 一件事, as 芝士's does. In a project with
 *  more than one person, a teammate looks a moment later. */
export function markDone(id: string): void {
  const t = thingById(id)
  if (!t?.copy || !canWork(t)) return
  const at = new Date().toISOString()
  t.status = 'wait'
  t.at = at
  t.takenBy = null
  t.last = `${NAMES[ME]}：改好了，等采纳`
  say(t, { id: `${id}d`, mark: `${NAMES[ME]} 改好了 · 等采纳`, at })
  toast('改好了：在这件事的「改动」里看，采纳了才进正式版')
  const p = projectOfChan(t.chan)
  if (!isShared(p)) return
  const who = reviewerOf(p)
  later(1600, () => {
    if (t.status !== 'wait') return
    say(t, { id: `${id}r`, who, at: new Date().toISOString(), text: '看了改动，可以。' })
    t.replies++
    t.last = `${NAMES[who]}：看了改动，可以。`
  })
}

/** 接手: a person takes over 芝士's work, in the same 工作台; 芝士 stops and says so in the 对话. */
export function takeOver(id: string): void {
  const t = thingById(id)
  if (!t || !canTake(t)) return
  const at = new Date().toISOString()
  t.takenBy = ME
  worked(t, ME)
  if (S.cheeseAt?.thing === id) S.cheeseAt = null
  t.last = `${NAMES[ME]} 接手了，芝士先停下`
  say(t, { id: nextId('k'), mark: `${NAMES[ME]} 接手`, at })
  say(t, {
    id: nextId('c'),
    who: 'cheese',
    at,
    text: '好，我先停下。工作台归你了，改、运行都行；改完点「交给芝士」，我接着做。',
  })
  toast('接手了：芝士先停下，你在同一个工作台里改')
}

/** 交给芝士, with a note: from a person who took 芝士's work over, or a person's own 一件事 handed on. 芝士 picks
 *  up the copy as it reads, runs it, and finishes. */
export function handToCheese(id: string, note: string): void {
  const t = thingById(id)
  if (!t?.copy || !canWork(t) || t.course) return
  const at = new Date().toISOString()
  t.takenBy = null
  t.doer = 'cheese'
  worked(t, 'cheese')
  S.hand = false
  t.last = '芝士：收到，接着做'
  say(t, { id: nextId('k'), mark: `${NAMES[ME]} 交给芝士`, at })
  if (note.trim()) say(t, { id: nextId('x'), who: ME, at, text: note.trim() })
  say(t, { id: nextId('c'), who: 'cheese', at, text: '收到，接着做。你改的都留着。' })
  t.replies += 2
  toast('交给芝士了')
  const path = Object.keys(t.copy)[0]!
  const full = path === 'eval/retrieval.py'
  play(id, [
    ...runActs(
      path,
      full
        ? {
            cmd: 'python -m eval.retrieval --data flickr30k --split 1k',
            out: /descending\s*=\s*True/.test(t.copy[path]!.text)
              ? 'encoding 1000 images, 5000 captions … done (38s)\nR@1 79.1  R@5 94.8  R@10 97.6'
              : 'encoding 1000 images, 5000 captions … done (38s)\nR@1 0.1  R@5 0.5  R@10 1.0',
          }
        : undefined
    ),
    doneAct,
  ])
}

/** The 一件事 whose copy changes this file, while it is being done or waits for 采纳. */
export function thingsOnFile(p: ProjectKey, path: string): Thing[] {
  return S.things.filter(
    (t) => projectOfChan(t.chan) === p && (t.status === 'run' || t.status === 'wait') && !!t.copy && path in t.copy
  )
}

/** A line of the 一件事's own conversation (演示一's task keeps its scripted lines; what is added goes after them). */
function say(t: Thing, line: ChatLine | MarkLine): void {
  if (t.talk) t.talk.push(line)
  else (S.extra[t.id] ??= []).push(line as ChatLine)
}

/** Lines [s, e) of one version replaced by `lines` in another. */
interface Hunk {
  s: number
  e: number
  lines: string[]
}

/** The changes from `a` to `b` as hunks over a's lines (a longest-common-subsequence walk; the files are short). */
function hunksOf(a: string[], b: string[]): Hunk[] {
  const L = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      L[i]![j] = a[i] === b[j] ? L[i + 1]![j + 1]! + 1 : Math.max(L[i + 1]![j]!, L[i]![j + 1]!)
  const out: Hunk[] = []
  let cur: Hunk | null = null
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      if (cur) out.push(cur)
      cur = null
      i++
      j++
      continue
    }
    cur ??= { s: i, e: i, lines: [] }
    if (i < a.length && (j === b.length || L[i + 1]![j]! >= L[i]![j + 1]!)) cur.e = ++i
    else cur.lines.push(b[j++]!)
  }
  if (cur) out.push(cur)
  return out
}

/** One stretch of a three-way merge: lines both sides agree on, or a block both changed differently. */
export type MergePart = { same: string[] } | { ours: string[]; theirs: string[] }

/** 正式版 as it reads now (ours) and the 一件事's copy (theirs), both from the copy's base: where they changed
 *  different lines both changes are kept; where they changed the same lines the block is left for a person. */
function merge3(base: string, ours: string, theirs: string): MergePart[] {
  const B = base.split('\n')
  const tagged = [
    ...hunksOf(B, ours.split('\n')).map((h) => ({ ...h, side: 'ours' as Side })),
    ...hunksOf(B, theirs.split('\n')).map((h) => ({ ...h, side: 'theirs' as Side })),
  ].sort((x, y) => x.s - y.s || x.e - y.e)
  const parts: MergePart[] = []
  const keep = (lines: string[]) => lines.length && parts.push({ same: lines })
  let at = 0
  let k = 0
  while (k < tagged.length) {
    const group = [tagged[k++]!]
    let gs = group[0]!.s
    let ge = group[0]!.e
    // A hunk joins the block when it overlaps it, or starts where it starts (two insertions at one place).
    while (k < tagged.length && (tagged[k]!.s < ge || tagged[k]!.s === gs)) {
      const h = tagged[k++]!
      group.push(h)
      gs = Math.min(gs, h.s)
      ge = Math.max(ge, h.e)
    }
    keep(B.slice(at, gs))
    const sideText = (side: Side) => {
      const out: string[] = []
      let c = gs
      for (const h of group.filter((x) => x.side === side)) {
        out.push(...B.slice(c, h.s), ...h.lines)
        c = h.e
      }
      return [...out, ...B.slice(c, ge)]
    }
    const o = sideText('ours')
    const t = sideText('theirs')
    const sides = new Set(group.map((h) => h.side))
    if (sides.size === 1) keep(sides.has('ours') ? o : t)
    else if (o.join('\n') === t.join('\n')) keep(o)
    else parts.push({ ours: o, theirs: t })
    at = ge
  }
  keep(B.slice(at))
  return parts
}

/** Where a 一件事's copy stands against 正式版 now, per file it changed: whether 正式版 moved since the copy was
 *  taken, who moved it, the blocks both changed, and the text 采纳 would write (blocks not yet picked: the copy's). */
export function copyState(t: Thing) {
  const p = projectOfChan(t.chan)
  return Object.entries(t.copy ?? {})
    .filter(([, c]) => c.text !== c.base)
    .map(([path, c]) => {
      const now = officialText(p, path)
      const stale = t.status !== 'done' && now !== c.base
      const parts = stale ? merge3(c.base, now, c.text) : [{ same: c.text.split('\n') }]
      const picks = t.picks?.[path] ?? {}
      let n = 0
      const lines = parts.flatMap((x) => ('same' in x ? x.same : picks[n++] === 'ours' ? x.ours : x.theirs))
      const blocks = parts.filter((x): x is { ours: string[]; theirs: string[] } => !('same' in x))
      const by = [
        ...new Set(S.history[p].filter((h) => h.at > (t.copyAt ?? '') && h.prevs && path in h.prevs).map((h) => h.who)),
      ]
      return {
        path,
        now,
        stale,
        by,
        blocks,
        picks,
        text: lines.join('\n'),
        open: blocks.length - Object.keys(picks).length,
      }
    })
}

/** The adopter's choice for one block both sides changed. */
export function pickSide(id: string, path: string, block: number, side: Side): void {
  const t = thingById(id)
  if (!t) return
  ;((t.picks ??= {})[path] ??= {})[block] = side
}

/** Who did a 一件事, as its 采纳 says it: 「芝士做的」, 「林晓做的」, 「芝士做的，林晓接手改过」, 「林晓开的头，芝士做完」,
 *  a team's 「林晓、周珂、陈思远做的」. */
function byText(t: Thing): string {
  const by = t.by?.length ? t.by : [t.doer ?? 'cheese']
  if (by.length === 1) return `${NAMES[by[0]!]}做的`
  if (by[0] === 'cheese')
    return `芝士做的，${by
      .slice(1)
      .map((h) => NAMES[h])
      .join('、')}接手改过`
  if (by.at(-1) === 'cheese')
    return `${by
      .slice(0, -1)
      .map((h) => NAMES[h])
      .join('、')}开的头，芝士做完`
  return `${by.map((h) => NAMES[h]).join('、')}做的`
}

/** 采纳, the one way a 一件事 reaches 正式版 (演示一's task, 芝士's, a person's own, a course's). Each file it
 *  changed becomes 正式版 as one 历史 entry that names the 一件事 and who did it, and 撤回 can take back. If
 *  正式版 moved meanwhile, nothing is overwritten: the copy first catches up with 正式版 (a line in the 一件事's
 *  conversation), and a block both changed takes the side the adopter picked; with a block not picked yet, 采纳 waits. */
export function acceptCopy(id: string): void {
  const t = thingById(id)
  if (!t?.copy || t.status !== 'wait') return
  const p = projectOfChan(t.chan)
  const states = copyState(t)
  if (states.some((x) => x.open > 0)) {
    toast('两边都改了同一处：先在「改动」里挑用哪边')
    return
  }
  const at = new Date().toISOString()
  const stale = states.filter((x) => x.stale)
  if (stale.length) {
    const kept = [...new Set(stale.flatMap((x) => x.by))].map((h) => NAMES[h]).join('、') || '别人'
    const picked = stale.reduce((n, x) => n + x.blocks.length, 0)
    const text = `跟上了最新的正式版：${kept}这期间的改动都留着${picked ? `，${picked} 处两边都改的用了${NAMES[ME]}挑的那边` : ''}。`
    say(
      t,
      t.doer === ME ? { id: `${id}u`, mark: `${NAMES[ME]} ${text}`, at } : { id: `${id}u`, who: 'cheese', at, text }
    )
  }
  const prevs: Record<string, string> = {}
  for (const x of states) {
    if (x.text === x.now) continue
    prevs[x.path] = x.now
    t.copy[x.path] = { base: x.now, text: x.text }
    S.official[fkey(p, x.path)] = x.text
  }
  t.status = 'done'
  t.at = at
  t.last = '已采纳 · 合进正式版'
  t.takenBy = null
  // 演示一's task says 采纳 in its own scripted conversation.
  if (t.talk) say(t, { id: `${id}y`, mark: `${NAMES[ME]} 采纳 · 已合进正式版`, at, ok: true })
  const paths = Object.keys(prevs)
  const adds = Object.keys(t.adds ?? {})
  const by = byText(t)
  const what = [paths.length ? `改了 ${paths.join('、')}` : '', adds.length ? `新增 ${adds.join('、')}` : '']
    .filter(Boolean)
    .join('，')
  if (what) log(p, ME, `采纳了「${t.title}」（${by}）：${what}`, { prevs, via: { thing: id, title: t.title, by } })
  toast(what ? '已采纳：进了正式版' : '已采纳：这件事和正式版一样，没有要改的')
}

/** A teammate's own small 一件事 on a code file, done and 采纳ed by them while the demo plays (王磊 in 演示二):
 *  its card in the file's channel, and its 采纳 in 历史, the same way as anyone's. */
export function peerChange(who: string, p: ProjectKey, path: string, title: string, text: string): void {
  const base = officialText(p, path)
  if (base === text) return
  const chan = chanForFile(p, path)
  const at = new Date().toISOString()
  const id = nextId('tw')
  S.things.unshift({
    id,
    title,
    chan,
    owner: who,
    doer: who,
    status: 'done',
    replies: 0,
    last: '已采纳 · 合进正式版',
    at,
    talk: [
      { id: `${id}k`, mark: `${NAMES[who]} 开了这件事 · 自己做`, at },
      { id: `${id}y`, mark: `${NAMES[who]} 采纳 · 已合进正式版`, at, ok: true },
    ],
    copy: { [path]: { base, text } },
    copyAt: at,
    runs: [],
    by: [who],
  })
  ;(S.chat[chan] ??= []).push({ id: `${id}m`, who, at, text: `改 \`${path}\``, thing: id })
  S.official[fkey(p, path)] = text
  const by = byText(thingById(id)!)
  log(p, who, `采纳了「${title}」（${by}）：改了 ${path}`, { prevs: { [path]: base }, via: { thing: id, title, by } })
}

// ---- 提交: a submission is made to the 题目 ----
//
// A submission belongs to the 题目, not to the 一件事 or the project: the
// dialog lists the 题目's 提交项, fills each FILE item from a version of the
// file and records the submission on the 题目, where the space shows it. The
// versions offered: the file as the work 一件事 (领取's 「做 Data Lab」) reads
// now, while it is still being done, then 正式版 as it reads now and each
// earlier one 历史 can name. A student submits straight from the work: the
// submission keeps the exact text and its version id, so nothing has to be
// 采纳ed first just to hand it in. How it is graded is the 题目's: 评测机评
// comes back at once, 老师评 waits for 张老师.

let seqSub = 0

/** One version of a project file to submit from: the work 一件事's, 正式版 now, then each earlier one 历史 can name. */
export interface FileVersion {
  id: string
  text: string
  /** When it came into effect and who made it; the starter code has the claim's time. */
  at: string
  who: string
  current: boolean
  start: boolean
  /** The 一件事 it is read from (its 工作台 as it reads now), when it is not a version of 正式版. */
  work?: string
}

/** The versions of a file, newest first: 正式版 now, then what it read before each change in 历史. */
export function versionsOf(p: ProjectKey, path: string): FileVersion[] {
  const out: FileVersion[] = []
  let text = officialText(p, path)
  const touching = S.history[p].filter((h) => h.fixed || (h.prevs && path in h.prevs))
  for (const h of touching) {
    out.push({ id: versionOf(text), text, at: h.at, who: h.who, current: !out.length, start: !!h.fixed })
    if (h.fixed) break
    text = h.prevs![path]!
  }
  if (!out.length) out.push({ id: versionOf(text), text, at: '', who: ME, current: true, start: false })
  // A change and its 撤回 give the same text twice: one entry per version.
  return out.filter((v, i) => out.findIndex((x) => x.id === v.id) === i)
}

/** Every version 提交 offers, the first one picked: the file in the work 一件事 while it is still being done (unless
 *  it reads the same as 正式版), then 正式版's. */
export function submitVersions(p: ProjectKey, path: string): FileVersion[] {
  const official = versionsOf(p, path)
  const t = workOf(p)
  const c = t?.copy?.[path]
  if (!t || !c || t.status === 'done') return official
  const id = versionOf(c.text)
  if (id === official[0]?.id) return official
  return [{ id, text: c.text, at: '', who: ME, current: false, start: false, work: t.title }, ...official]
}

/** 提交 on a 题目: for a 题单 one problem, the one whose file is open, else the first not passed. */
export function openSubmit(topicId: string, unit?: string): void {
  const t = topicById(topicId)
  if (!t?.project || !S.claimed.includes(t.project)) return
  let u = unit ?? topicId
  if (!unit && t.category === '题单') {
    // The problem open in the work 一件事's 工作台, or in 项目文件.
    const w = workOf(t.project)
    const file = (S.bench && w && S.thing === w.id && benchFileOf(w)) || S.file
    const open = SET.find((x) => S.project === t.project && file.startsWith(`${x.id}/`))
    u = open?.id ?? SET.find((x) => !passed(latestOf(topicId, x.id)))?.id ?? SET[0]!.id
  }
  S.submitAsk = { topic: topicId, unit: u }
}

/** The dialog's 提交: the chosen version of the FILE item and the TEXT items go to the 题目, graded its way. */
export function submitTopic(
  topicId: string,
  unit: string,
  file: FileVersion,
  texts: { prompt: string; text: string }[]
) {
  const t = topicById(topicId)
  if (!t?.project) return
  const p = t.project
  const item = submitItems(t, unit).find((x) => x.type === 'FILE')!
  const team = projectTeam(p)
  const list = S.subs[topicId]!
  const n = list.filter((x) => x.unit === unit).length + 1
  const id = `s${seqSub++}`
  list.unshift({
    id,
    topic: topicId,
    unit,
    n,
    at: new Date().toISOString(),
    who: ME,
    team: team.personal ? undefined : team.handle,
    path: item.path!,
    version: file.id,
    code: file.text,
    texts,
  })
  S.submitAsk = null
  const name = t.category === '题单' ? UNITS[unit]!.title : t.title
  // The reactive copy: what is set on it later reaches every view.
  const sub = list.find((x) => x.id === id)!
  if (t.grading === 'judge') {
    toast(`已提交「${name}」第 ${n} 次：评测中`)
    later(1500, () => {
      const v = UNITS[unit]!.judge(file.text)
      sub.verdict = v
      // I just submitted it and the toast says what it got: not 「有新批改」 on top of that.
      if (sub.who === ME) S.seen[id] = true
      toast(v.score === v.total ? `通过了：${name}` : `「${name}」第 ${n} 次：${v.score}/${v.total}`)
    })
    return
  }
  toast(`已提交「${name}」：等张老师批改`)
  // The teacher's side is not in the demo; her review arrives a little later to show the loop closing.
  later(6000, () => {
    sub.review = { ...reviewShell(file.text, texts[0]?.text ?? ''), at: new Date().toISOString() }
    toast(`张老师批改了「${name}」：${sub.review.score}/${sub.review.total}`)
  })
}

/** The latest submission of each unit decides where it stands (以截止前最后一次为准). */
export const latestOf = (topicId: string, unit: string) => S.subs[topicId]?.find((x) => x.unit === unit)

/** A submission that has passed: full marks from the judge. */
const full = (v?: Verdict) => !!v && v.score === v.total
export const passed = (s?: Submission) => full(s?.verdict)

/** What a submission got, in a few words: the score, or 评测中 / 待老师评 while it waits. */
export function resultOf(s: Submission): { text: string; tone: 'ok' | 'bad' | 'wait' } {
  if (s.verdict) return { text: `${s.verdict.score}/${s.verdict.total}`, tone: full(s.verdict) ? 'ok' : 'bad' }
  if (s.review) return { text: `${s.review.score}/${s.review.total}`, tone: s.review.score >= 60 ? 'ok' : 'bad' }
  return topicById(s.topic)?.grading === 'teacher'
    ? { text: '待老师评', tone: 'wait' }
    : { text: '评测中…', tone: 'wait' }
}

/** A result on the 题目 I have not looked at yet: the teacher's review, or a verdict on someone else's submission. */
export function unseenResult(topicId: string): Submission | null {
  return S.subs[topicId]?.find((x) => (x.verdict || x.review) && !S.seen[x.id]) ?? null
}

/** Opening the 题目 (or folding the card that announced it) counts as having looked. */
export function markSeen(topicId: string): void {
  for (const x of S.subs[topicId] ?? []) if (x.verdict || x.review) S.seen[x.id] = true
}

/** Where I stand on a 题目, for the project's origin card: 未提交, 已通过 3/5, or the latest submission. */
export function myStatus(topicId: string): { text: string; sub?: string; tone: 'new' | 'ok' | 'bad' | 'wait' } {
  const list = S.subs[topicId] ?? []
  if (topicById(topicId)?.category === '题单') {
    if (!list.length) return { text: '未提交', tone: 'new' }
    const n = SET.filter((x) => passed(latestOf(topicId, x.id))).length
    return {
      text: `已通过 ${n}/${SET.length}`,
      sub: `提交了 ${list.length} 次`,
      tone: n === SET.length ? 'ok' : 'wait',
    }
  }
  const last = list[0]
  if (!last) return { text: '未提交', tone: 'new' }
  const r = resultOf(last)
  return { text: r.text, sub: `已提交 · 第 ${last.n} 次`, tone: r.tone }
}

// ---- 项目文件 ----

/** Open a file in 项目文件, unfolding its folder. */
export function openFile(path: string, extra: Partial<typeof S> = {}): void {
  const dir = path.includes('/') ? path.slice(0, path.indexOf('/')) : null
  const list = S.collapsed[S.project]
  if (dir && list?.includes(dir)) list.splice(list.indexOf(dir), 1)
  go('files', { file: path, viewSub: null, ...extra })
}

/** Open the exact version a submission pinned, read-only, in 项目文件's code view (from the 题目's 提交记录). */
export function openVersion(id: string): void {
  const sub = Object.values(S.subs)
    .flat()
    .find((x) => x.id === id)
  const p = sub && topicById(sub.topic)?.project
  if (!sub || !p) return
  switchProject(p)
  openFile(sub.path, { viewSub: id })
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
    openWork(p, owner.personal ? [ME] : owner.members)
    toast(
      owner.personal
        ? `领取了「${t.title}」：项目「${CLAIMED[p].name}」建在你自己的团队下，开好了一件事「${WORK_TITLE[p]}」`
        : `「${owner.name}」领取了「${t.title}」：${owner.members.length} 个人一起做「${WORK_TITLE[p]}」`
    )
  }
  switchProject(p)
}

/** The 一件事 领取 opens: the work itself, done by whoever claimed it (each of a team), its 工作台 holding a
 *  copy of every code file the 题目 brought. 芝士 only gives hints in its 对话. Its card sits under 芝士's
 *  welcome in the project's # 综合. */
function openWork(p: ClaimKey, people: string[]): void {
  const at = new Date().toISOString()
  const id = `w-${p}`
  const code = PROJECT_FILES[p].filter((f) => f.kind === 'code')
  S.things.unshift({
    id,
    title: WORK_TITLE[p],
    chan: CLAIMED[p].chan,
    owner: ME,
    doer: ME,
    team: people.length > 1 ? people : undefined,
    course: true,
    status: 'run',
    replies: 0,
    last: people.length > 1 ? `${people.map((h) => NAMES[h]).join('、')}在做` : `${NAMES[ME]}在做`,
    at,
    talk: [
      {
        id: `${id}k`,
        mark:
          people.length > 1 ? `${people.map((h) => NAMES[h]).join('、')} 一起做` : `${NAMES[ME]} 领取了题目 · 自己做`,
        at,
      },
    ],
    copy: Object.fromEntries(code.map((f) => [f.path, { base: f.starter!, text: f.starter! }])),
    copyAt: at,
    runs: [],
    by: people,
  })
}

/** Where a 题目 stands for me in the space's list: 未领取, 进行中 · 项目名, or 已提交 with the latest result. */
export function standing(topicId: string): { text: string; tone: 'new' | 'busy' | 'done' } {
  const p = topicById(topicId)?.project
  if (!p || !S.claimed.includes(p)) return { text: '未领取', tone: 'new' }
  const st = myStatus(topicId)
  if (st.tone === 'new') return { text: `进行中 · ${CLAIMED[p].name}`, tone: 'busy' }
  // Green only when it is through: a failing score or one still waiting reads as work in progress.
  return { text: p === 'week3' ? st.text : `已提交 · ${st.text}`, tone: st.tone === 'ok' ? 'done' : 'busy' }
}

let seq = 0
const nextId = (p: string) => `${p}${Date.now().toString(36)}${seq++}`

/** The channel composer: an @芝士 opens a 讨论 under the message; anything else is just said. */
export function send(): void {
  const text = S.typing.trim()
  if (!text) return
  // A file the message names (`eval/retrieval.py`): an @芝士 about it opens a 讨论 that can become a change.
  const ref =
    [...text.matchAll(/`([^`]+)`/g)].map((m) => fileRef(S.project, m[1]!)).find((r): r is FileAt => !!r) ?? null
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
  // A code file asked about in the research project: a 讨论 of its own about it.
  if (ref && text.includes('<@cheese>') && pathsOf('research').includes(ref.path) && CODE[ref.path]) {
    discussCode(S.chan, text, ref)
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
  // Read back through S, not the `??=` result: a fresh array is only reactive once it is in S.
  S.extra[t.id] ??= []
  const list = S.extra[t.id]!
  list.push({ id: nextId('x'), who: ME, at: new Date().toISOString(), text, replyTo: S.thingReplyTo ?? undefined })
  S.thingReplyTo = null
  t.replies++
  t.last = `${NAMES[ME]}：${text.replace(/<@cheese>/g, '@芝士')}`
  t.at = new Date().toISOString()
  // A course's 一件事: 芝士 answers an @ with a hint, never with code, and never works on it.
  const asked = /<@cheese>|@芝士/.test(text)
  const asks = t.course ? asked : asked || t.status !== 'talk'
  if (!asks) return
  setTimeout(() => {
    const answer = t.course
      ? hintFor(text)
      : t.status === 'talk'
        ? '好，记下了，开工时按这个来。'
        : doingNow(t) === 'cheese'
          ? '收到，我把这条加进计划，做完一起给你看。'
          : t.status === 'run'
            ? '收到。现在是你在做，要我接着做就点「交给芝士」。'
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

/** The 一件事 a 开工 is about: the open one (演示一's task when none is open). */
const kickable = () => {
  const t = thingById(S.thing) ?? t4()
  return t?.status === 'talk' && (t.id === 't4' || !!t.copy) ? t : null
}

export function kickoff(): void {
  if (kickable()) S.sheet = true
}

export function confirmKick(): void {
  const t = kickable()
  if (!t) return
  S.sheet = false
  if (t.id !== 't4') return startCopy(t)
  // 演示一's task: 芝士's copy of the two code files it changes; the plan steps fill it in (liveStep).
  const at = new Date().toISOString()
  t.copy = Object.fromEntries(
    Object.entries(CODE)
      .filter(([, c]) => c.after)
      .map(([path]) => [path, { base: officialText('research', path), text: officialText('research', path) }])
  )
  t.copyAt = at
  t.runs = []
  t.by = ['cheese']
  t.status = 'run'
  t.last = '芝士：开始改了，先对齐预处理'
  t.at = at
  S.morph = true
  liveStep(0)
  toast('开工了，交给芝士')
  setTimeout(() => (S.morph = false), 1200)
}

/** Which file of 演示一's task 芝士 changes at each plan step, and the line it is on. */
const T4_EDITS: { path: string; line: number }[] = [
  { path: 'eval/preprocess.py', line: 7 },
  { path: 'eval/retrieval.py', line: 18 },
]

/** 演示一's task at plan step `i` (0–5): the files of its copy as far as 芝士 has got, the runs of the steps
 *  done and under way (T4_STEPS), where its caret is, and at the end the result table it adds. */
export function liveStep(i: number): void {
  const t = t4()
  if (!t?.copy) return
  S.live = i
  T4_EDITS.forEach((e, k) => {
    const c = t.copy![e.path]!
    c.text = i > k ? CODE[e.path]!.after!.join('\n') : c.base
  })
  const upTo = Math.min(i + 1, T4_STEPS.length)
  t.runs = T4_STEPS.slice(0, upTo)
    .flat()
    .flatMap((s, k) =>
      s.kind === 'bash' ? [{ id: `t4r${k}`, who: 'cheese', at: t.at, cmd: s.command, out: s.output }] : []
    )
  const edit = T4_EDITS[i]
  S.cheeseAt = edit && i < 5 ? { thing: 't4', ...edit } : null
  if (i >= 4) t.adds = { 'results/eval_results.md': RESULTS_MD }
}

export function finish(): void {
  const t = t4()
  if (!t) return
  liveStep(5)
  t.status = 'wait'
  t.last = '芝士：Flickr30K R@1 = 79.1，等你采纳'
  t.at = new Date().toISOString()
}

/** The plan 演示一's task follows, ticked as far as it has got. */
export const t4Plan = () =>
  PLAN.map((subject, i) => ({ subject, done: (t4()?.status ?? 'talk') !== 'run' || i < S.live }))

/** Tell 芝士 what to change in its copy: the task's own conversation, with an @ ready. */
export function tellCheese(): void {
  S.rtab = 'chat'
  S.bench = false
  if (!S.thingTyping.includes('<@cheese>')) S.thingTyping = `<@cheese> ${S.thingTyping}`
}

export function suggAccept(): void {
  if (S.suggAccepted) return
  S.suggAccepted = true
  log('research', ME, '接受了芝士对《组会纪要 11-07》的建议', { about: MINUTES_DOC })
  toast('建议已接受，进了正式版')
}

export function suggReject(): void {
  if (S.suggAccepted || S.suggRejected) return
  S.suggRejected = true
  log('research', ME, '拒绝了芝士对《组会纪要 11-07》的建议', { about: MINUTES_DOC })
  toast('建议已拒绝，正式版不变')
}

/** 撤回 one entry of the open project's 历史: an 采纳 puts back the text each file had before it. */
export function undo(index: number): void {
  const list = S.history[S.project]
  const h = list[index]
  if (!h || h.fixed) return
  list.splice(index, 1)
  if (h.what.startsWith('接受了芝士对')) S.suggAccepted = false
  if (h.what.startsWith('拒绝了芝士对')) S.suggRejected = false
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
      const left = SET.filter((x) => !passed(latestOf(t.id, x.id))).length
      if (!left) continue
      out.push({
        id: t.id,
        title: `${t.title}：还差 ${left} 题`,
        sub: `${SET.length - left}/${SET.length} 题通过`,
        chip,
        open: () => openDue(t.id, '题单.md'),
      })
      continue
    }
    const last = latestOf(t.id, t.id)
    // Submitted to a 老师评 题目 is done on my side; a judged one is done once it passes.
    if (last && (t.grading === 'teacher' || passed(last))) continue
    const file = PROJECT_FILES[p].find((f) => f.unit === p)!.path
    out.push({
      id: t.id,
      title: `${t.title} ${chip}`,
      sub: last ? `最近一次 ${resultOf(last).text}` : '还没提交',
      chip,
      open: () => openDue(t.id, file),
    })
  }
  return out
}

/** A deadline row of a claimed 题目 opens the project's own file, with the 题目's card under the
 *  sidebar header opened (提交 is there). */
function openDue(topicId: string, file: string): void {
  S.cardOpen[topicId] = true
  switchProject(topicById(topicId)!.project!)
  openFile(file)
}
