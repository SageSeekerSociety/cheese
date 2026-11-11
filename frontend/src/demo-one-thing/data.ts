// The demo's sample project: who is in it, what the channels say, the task's
// changes and the official versions of the files. All of it is made up; the
// screens that draw it are the product's own components.
//
// Every time is relative to the moment the page opened, so the clock on a
// message, the 「12 分钟前」 on a card and the day lines agree with the
// viewer's own clock whenever the demo is watched.
import type { FileDiff } from '@/lib/diff'

import { parseDiffLines, splitDiffByFile } from '@/lib/diff'
import { diffOf } from '@/views/demo/demoPanels'

export interface Person {
  handle: string
  name: string
  agent?: boolean
}

export const ME = 'linxiao'

export const PEOPLE: Person[] = [
  { handle: 'linxiao', name: '林晓' },
  { handle: 'wanglei', name: '王磊' },
  { handle: 'chensiyuan', name: '陈思远' },
  { handle: 'zhaoyutong', name: '赵雨桐' },
  { handle: 'zhouke', name: '周珂' },
  { handle: 'cheese', name: '芝士', agent: true },
]

/** The course's teachers: they write the space's 公告 and 资料库. */
export const COURSE_PEOPLE: Person[] = [
  { handle: 'zhanglaoshi', name: '张老师' },
  { handle: 'lizhujiao', name: '李助教' },
]

export const NAMES: Record<string, string> = Object.fromEntries(
  [...PEOPLE, ...COURSE_PEOPLE].map((p) => [p.handle, p.name])
)

export const CHANNELS = [
  {
    id: 'c-general',
    title: '综合',
    about: '项目里的大小事、机器和排期',
    members: ['linxiao', 'wanglei', 'chensiyuan', 'zhaoyutong', 'zhouke'],
  },
  {
    id: 'c-papers',
    title: '论文精读',
    about: '每周一篇，读完把笔记放进 docs/papers',
    members: ['linxiao', 'wanglei', 'chensiyuan'],
  },
  {
    id: 'c-exp',
    title: '实验记录',
    about: '实验结果与评测口径，数字以 results/ 为准',
    members: ['linxiao', 'wanglei', 'chensiyuan', 'zhaoyutong'],
  },
  {
    id: 'c-meeting',
    title: '组会',
    about: '周五组会的安排和汇报材料',
    members: ['linxiao', 'wanglei', 'chensiyuan', 'zhaoyutong', 'zhouke'],
  },
]

export const PROJECT = { team: '视觉语言组', teamHandle: 'vl-group', name: '多模态检索论文复现' }

/** The course 空间: not a project. It has its own icon on the rail (`short` is what the icon
 *  shows) and is listed under 空间 on 首页; it holds the 题目 the teacher published, 公告 and 资料库.
 *  `brief` names it where a line in a project's sidebar or header has no room for the full name. */
export const SPACE = {
  id: 1,
  name: '计算机系统基础（ICS 2026）',
  short: 'ICS',
  brief: 'ICS 2026',
  org: '计算机学院',
  teacher: '张老师',
}

/** A team owns projects. Everyone has a personal one (just them); a claim by a team makes the team's project. */
export interface DemoTeam {
  handle: string
  name: string
  personal?: boolean
  members: string[]
}

export const TEAMS: DemoTeam[] = [
  { handle: 'linxiao', name: '林晓', personal: true, members: ['linxiao'] },
  { handle: 'vl-group', name: PROJECT.team, members: PEOPLE.filter((p) => !p.agent).map((p) => p.handle) },
  { handle: 'ics-g7', name: 'ICS 第 7 组', members: ['linxiao', 'zhouke', 'chensiyuan'] },
]
export const teamOf = (handle: string) => TEAMS.find((t) => t.handle === handle)!

/** The projects 领取 creates: one per team per 题目, an ordinary project owned by that team. */
export type ClaimKey = 'datalab' | 'week3' | 'shelllab'
export const CLAIMS: ClaimKey[] = ['datalab', 'week3', 'shelllab']
export type ProjectKey = 'research' | ClaimKey

export const CLAIMED: Record<ClaimKey, { name: string; team: string; railId: string; chan: string; slug: string }> = {
  datalab: { name: 'Data Lab', team: 'linxiao', railId: 'p-dl', chan: 'dl-general', slug: 'datalab' },
  week3: { name: '第 3 周题单', team: 'linxiao', railId: 'p-w3', chan: 'w3-general', slug: 'week3' },
  shelllab: { name: 'Shell Lab 大作业', team: 'ics-g7', railId: 'p-sh', chan: 'sh-general', slug: 'shelllab' },
}

const claimedChannel = (k: ClaimKey) => ({
  id: CLAIMED[k].chan,
  title: '综合',
  about: '这个项目里的事；芝士在这里只给提示',
  members: teamOf(CLAIMED[k].team).members,
})

export const isClaim = (p: ProjectKey): p is ClaimKey => p === 'datalab' || p === 'week3' || p === 'shelllab'

/** A project with more than one person in it: people edit 正式版 together and can open a branch. */
export const isShared = (p: ProjectKey) => p === 'research' || (isClaim(p) && !teamOf(CLAIMED[p].team).personal)

/** The people in a project: its team's members (芝士 is added where a list needs it). */
export const peopleOf = (p: ProjectKey) =>
  p === 'research' ? PEOPLE : PEOPLE.filter((x) => projectTeam(p).members.includes(x.handle))

/** The team a project belongs to. */
export const projectTeam = (p: ProjectKey) => (p === 'research' ? teamOf('vl-group') : teamOf(CLAIMED[p].team))

/** The header of a project's sidebar: the team that owns it, and its name. */
export function projectMeta(p: ProjectKey) {
  if (p === 'research') return PROJECT
  const team = teamOf(CLAIMED[p].team)
  return { team: team.name, teamHandle: team.handle, name: CLAIMED[p].name }
}

/** The clone address for 用本地编辑器打开: under the owning team. */
export const gitUrl = (p: ProjectKey) =>
  p === 'research'
    ? 'https://okcheese.com/git/vl-group/mm-retrieval.git'
    : `https://okcheese.com/git/${CLAIMED[p].team}/${CLAIMED[p].slug}.git`

export type Channel = (typeof CHANNELS)[number]

export const channelsOf = (p: ProjectKey): Channel[] => (p === 'research' ? CHANNELS : [claimedChannel(p)])
export const ALL_CHANNELS: Channel[] = [...CHANNELS, ...CLAIMS.map(claimedChannel)]
export const channelOf = (id: string): Channel | undefined => ALL_CHANNELS.find((c) => c.id === id)
/** Which project a channel belongs to. */
export const projectOfChan = (id: string): ProjectKey => CLAIMS.find((k) => CLAIMED[k].chan === id) ?? 'research'

/** The other projects on the rail, and the team each belongs to. They are not in the demo. */
export const OTHER_PROJECTS = [
  { id: 'p-kg', name: '知识图谱问答', badge: 3, team: 'vl-group' },
  { id: 'p-data', name: '数据清洗流水线', badge: 0, team: 'vl-group' },
  { id: 'p-thesis', name: '毕业论文', badge: 0, team: 'linxiao' },
]

// ---- clock ----

/** The moment the page opened: every sample time is counted back from it. */
export const NOW = Date.now()

export const minsAgo = (m: number) => new Date(NOW - m * 60_000).toISOString()

/** A clock time on a day before today (`days` back), in the viewer's own zone. */
export function dayAt(days: number, h: number, m: number): string {
  const d = new Date(NOW)
  d.setDate(d.getDate() - days)
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}

export const ASK = '<@cheese> 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10'

export const PLAN = [
  '对齐图像预处理（224，BICUBIC，center crop）',
  '统一 eval/retrieval.py，输出 R@1/5/10',
  '跑 Flickr30K 1K',
  '跑 COCO 5K',
  '整理结果表',
]

export const RESULTS_MD = `| 测试集 | R@1 | R@5 | R@10 |
| --- | --- | --- | --- |
| Flickr30K 1K | 79.1 | 94.8 | 97.6 |
| COCO 5K | 50.3 | 74.9 | 83.2 |

统一预处理后 Flickr30K R@1 = 79.1，和论文的 79.3 基本对上；COCO 5K 比论文低 0.5。`

/** The task's own changes, as a diff the product's diff viewer reads. */
export const TASK_DIFFS: FileDiff[] = splitDiffByFile(
  diffOf({
    files: [
      {
        path: 'eval/preprocess.py',
        diff: [
          ' import torchvision.transforms as T',
          '+from torchvision.transforms import InterpolationMode',
          ' ',
          ' ',
          ' def preprocess(img):',
          '     return T.Compose([',
          '-        T.Resize(256),',
          '+        T.Resize(224, interpolation=InterpolationMode.BICUBIC),',
          '+        T.CenterCrop(224),',
          '         T.ToTensor(),',
          '     ])(img)',
        ],
      },
      {
        path: 'eval/retrieval.py',
        diff: [
          '     sims = image_feats @ text_feats.T',
          '-    return recall_at_1(sims)',
          '+    return {',
          '+        "R@1": recall_at_k(sims, 1),',
          '+        "R@5": recall_at_k(sims, 5),',
          '+        "R@10": recall_at_k(sims, 10),',
          '+    }',
          '+',
          '+',
          '+def recall_at_k(sims, k):',
          '+    ranks = sims.argsort(dim=1, descending=True)[:, :k]',
          '+    hits = (ranks == torch.arange(len(sims))[:, None]).any(dim=1)',
          '+    return round(hits.float().mean().item() * 100, 1)',
        ],
      },
      {
        path: 'results/eval_results.md',
        status: 'added',
        diff: RESULTS_MD.split('\n').map((l) => `+${l}`),
      },
    ],
  } as Parameters<typeof diffOf>[0])
)

/** The official version of a code file, drawn by the same diff viewer with every line as context. */
export function asContext(path: string, lines: string[]) {
  return parseDiffLines(
    [`diff --git a/${path} b/${path}`, `--- a/${path}`, `+++ b/${path}`, `@@ -1,${lines.length} +1,${lines.length} @@`]
      .concat(lines.map((l) => ` ${l}`))
      .join('\n')
  )
}

/** Two versions of one file as a diff the product's viewer reads: the changed lines with three lines
 *  around each (a longest-common-subsequence walk; the demo's files are a few dozen lines). */
export function diffText(path: string, before: string, after: string): FileDiff {
  const a = before.split('\n')
  const b = after.split('\n')
  const L = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--)
    for (let j = b.length - 1; j >= 0; j--)
      L[i]![j] = a[i] === b[j] ? L[i + 1]![j + 1]! + 1 : Math.max(L[i + 1]![j]!, L[i]![j + 1]!)
  // Every line once, as git prints it, with its old and new line numbers.
  const ops: { line: string; o: number; n: number }[] = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) ops.push({ line: ` ${a[i++]}`, o: i, n: ++j })
    else if (i < a.length && (j === b.length || L[i + 1]![j]! >= L[i]![j + 1]!))
      ops.push({ line: `-${a[i++]}`, o: i, n: j })
    else ops.push({ line: `+${b[j++]}`, o: i, n: j })
  }
  // Keep three lines of context around each change; changes closer than that share a hunk.
  const keep = ops.map(() => false)
  ops.forEach((op, k) => {
    if (op.line[0] === ' ') return
    for (let m = Math.max(0, k - 3); m <= Math.min(ops.length - 1, k + 3); m++) keep[m] = true
  })
  const out = [`diff --git a/${path} b/${path}`, `--- a/${path}`, `+++ b/${path}`]
  for (let k = 0; k < ops.length; ) {
    if (!keep[k]) {
      k++
      continue
    }
    let end = k
    while (end < ops.length && keep[end]) end++
    const hunk = ops.slice(k, end)
    const olds = hunk.filter((x) => x.line[0] !== '+').length
    const news = hunk.filter((x) => x.line[0] !== '-').length
    const first = hunk[0]!
    const oStart = first.line[0] === '+' ? first.o + 1 : first.o
    const nStart = first.line[0] === '-' ? first.n + 1 : first.n
    out.push(`@@ -${oStart},${olds} +${nStart},${news} @@`, ...hunk.map((x) => x.line))
    k = end
  }
  return splitDiffByFile(out.join('\n'))[0]!
}

const PREPROCESS_OLD = [
  'import torchvision.transforms as T',
  '',
  '',
  'def preprocess(img):',
  '    return T.Compose([',
  '        T.Resize(256),',
  '        T.ToTensor(),',
  '    ])(img)',
]
const PREPROCESS_NEW = [
  'import torchvision.transforms as T',
  'from torchvision.transforms import InterpolationMode',
  '',
  '',
  'def preprocess(img):',
  '    return T.Compose([',
  '        T.Resize(224, interpolation=InterpolationMode.BICUBIC),',
  '        T.CenterCrop(224),',
  '        T.ToTensor(),',
  '    ])(img)',
]
const RETRIEVAL_HEAD = [
  'import torch',
  '',
  'from eval.preprocess import preprocess',
  '',
  '',
  'def encode_all(model, loader):',
  '    image_feats, text_feats = [], []',
  '    with torch.no_grad():',
  '        for images, texts in loader:',
  '            image_feats.append(model.encode_image(preprocess(images)))',
  '            text_feats.append(model.encode_text(texts))',
  '    return torch.cat(image_feats), torch.cat(text_feats)',
  '',
  '',
  'def evaluate(model, loader):',
  '    image_feats, text_feats = encode_all(model, loader)',
  '    sims = image_feats @ text_feats.T',
]

/** Code files on the official version: path → lines before / after the task is accepted. */
export const CODE: Record<string, { before: string[]; after?: string[] }> = {
  'eval/preprocess.py': { before: PREPROCESS_OLD, after: PREPROCESS_NEW },
  'eval/retrieval.py': {
    before: RETRIEVAL_HEAD.concat(['    return recall_at_1(sims)']),
    after: RETRIEVAL_HEAD.concat([
      '    return {',
      '        "R@1": recall_at_k(sims, 1),',
      '        "R@5": recall_at_k(sims, 5),',
      '        "R@10": recall_at_k(sims, 10),',
      '    }',
      '',
      '',
      'def recall_at_k(sims, k):',
      '    ranks = sims.argsort(dim=1, descending=True)[:, :k]',
      '    hits = (ranks == torch.arange(len(sims))[:, None]).any(dim=1)',
      '    return round(hits.float().mean().item() * 100, 1)',
    ]),
  },
  'configs/clip_vit_b32.yaml': {
    before: [
      'model:',
      '  name: ViT-B/32',
      '  pretrained: openai',
      'data:',
      '  flickr30k: /data/flickr30k',
      '  coco: /data/coco2017',
      '  batch_size: 256',
      '  num_workers: 8',
      'eval:',
      '  splits: [flickr30k_1k, coco_5k]',
    ],
  },
  'scripts/download_coco.sh': {
    before: [
      '#!/usr/bin/env bash',
      'set -euo pipefail',
      '',
      'DEST=${1:-/data/coco2017}',
      'mkdir -p "$DEST" && cd "$DEST"',
      'wget -c http://images.cocodataset.org/zips/val2017.zip',
      'wget -c http://images.cocodataset.org/annotations/annotations_trainval2017.zip',
      'unzip -q val2017.zip && unzip -q annotations_trainval2017.zip',
    ],
  },
}

export interface FileEntry {
  path: string
  kind: 'doc' | 'word' | 'code' | 'result'
  bytes: number
  /** A channel's own document: the same one the channel shows under 文档. */
  chan?: string
}

export const FILES: FileEntry[] = [
  { path: 'README.md', kind: 'doc', bytes: 1840 },
  { path: 'docs/组会纪要-11-07.md', kind: 'doc', bytes: 1214 },
  { path: 'docs/评测口径说明.md', kind: 'doc', bytes: 906 },
  { path: 'docs/papers/BLIP-精读.md', kind: 'doc', bytes: 4410 },
  { path: 'docs/papers/CLIP-精读.md', kind: 'doc', bytes: 5127 },
  ...CHANNELS.map((c) => ({ path: `docs/频道/${c.title}.md`, kind: 'doc' as const, bytes: 1200, chan: c.id })),
  { path: 'report/中期报告.docx', kind: 'word', bytes: 48_211 },
  { path: 'configs/clip_vit_b32.yaml', kind: 'code', bytes: 236 },
  { path: 'eval/preprocess.py', kind: 'code', bytes: 312 },
  { path: 'eval/retrieval.py', kind: 'code', bytes: 1032 },
  { path: 'scripts/download_coco.sh', kind: 'code', bytes: 341 },
  { path: 'results/eval_results.md', kind: 'result', bytes: 402 },
]

export const MINUTES_DOC = 'docs/组会纪要-11-07.md'

/** 组会纪要 as HTML: item 3 carries 芝士's pending suggestion as the editor's own suggestion marks. */
export function minutesHtml(accepted: boolean): string {
  const third = accepted
    ? '下周组会前把 COCO 5K 的结果更新到这里。'
    : '<del data-suggestion="cheese:m3">（待补）</del><ins data-suggestion="cheese:m3">下周组会前把 COCO 5K 的结果更新到这里。</ins>'
  return `<h2>组会纪要 11-07</h2>
<p>到会：林晓、王磊、陈思远、赵雨桐。周珂请假。</p>
<ol>
<li><p>评测口径：统一用 <code>eval/retrieval.py</code>，COCO 用 5K 测试集。</p></li>
<li><p>下周：王磊复现 BLIP 基线，陈思远补长描述截断实验。</p></li>
<li><p>${third}</p></li>
<li><p></p></li>
</ol>`
}

export const DOC_HTML: Record<string, string> = {
  'README.md': `<h1>多模态检索论文复现</h1>
<p>复现 CLIP / BLIP 在 Flickr30K、COCO 上的零样本图文检索，并做长描述截断的消融。</p>
<h3>目录</h3>
<ul><li><p><code>eval/</code>：评测脚本，入口 <code>python -m eval.retrieval</code></p></li><li><p><code>configs/</code>：模型和数据路径</p></li><li><p><code>results/</code>：每次采纳的结果表</p></li><li><p><code>docs/</code>：组会纪要、精读笔记、评测口径</p></li></ul>
<p>数据放在 A100 机器的 <code>/data</code> 下，下载脚本见 <code>scripts/download_coco.sh</code>。</p>`,
  'docs/评测口径说明.md': `<h2>评测口径说明</h2>
<p>检索评测统一输出 R@1 / R@5 / R@10，图→文和文→图都报，表里默认写文→图。</p>
<ul><li><p>Flickr30K 用 1K 测试集。</p></li><li><p>COCO 用 5K 测试集，不用 1K。</p></li><li><p>图像预处理：224，BICUBIC，center crop。</p></li><li><p>文本超过 77 个 token 直接截断，截断比例单独记。</p></li></ul>`,
  'docs/papers/BLIP-精读.md': `<h2>BLIP 精读</h2>
<p><strong>一句话</strong>：用 captioner 给网页图片重新写描述、再用 filter 去掉噪声描述（CapFilt），数据干净了，检索和描述都涨。</p>
<ul><li><p>模型：ViT 图像编码器 + 一个三用的文本端（ITC / ITM / LM）。</p></li><li><p>COCO 5K 检索 R@1 = 80.6（ViT-L，微调后），零样本 Flickr30K R@1 = 94.8。</p></li><li><p>复现要点：ITM 的 hard negative 从同一个 batch 里按相似度采。</p></li></ul>
<p>待讨论：CapFilt 那一步我们算力不够，先用官方放出的 129M 过滤后数据。</p>`,
  'docs/papers/CLIP-精读.md': `<h2>CLIP 精读</h2>
<p>对比学习，4 亿图文对，batch 32768。零样本检索靠 prompt「a photo of a {}」。</p>
<ul><li><p>ViT-B/32 零样本 Flickr30K R@1 = 79.3（图→文 88.0）。</p></li><li><p>文本端最长 77 个 token：长描述会被截断，这是我们消融的出发点。</p></li></ul>`,
}

export const WORD_COMPARISON = {
  kind: 'word',
  new_file: false,
  identical: false,
  changed: 2,
  rows: [
    { op: 'same', text: '一、研究进展' },
    {
      op: 'changed',
      pieces: [
        { op: 'equal', text: '目前已复现 CLIP 零样本检索，Flickr30K R@1 ' },
        { op: 'delete', text: '为 78.2' },
        { op: 'insert', text: '为 79.1，与论文报告的 79.3 基本一致' },
        { op: 'equal', text: '。' },
      ],
    },
    { op: 'added', text: '评测脚本已统一，见项目文件 eval/retrieval.py。' },
    { op: 'same', text: '二、下一步计划' },
    { op: 'same', text: '复现 BLIP 基线；做长描述截断对检索的影响。' },
  ],
}

// ---- 频道文档 ----

/** The part of a channel's document people wrote themselves: its text, who edited it last and when. */
export const CHAN_DOCS: Record<string, { html: string; by: string; at: string }> = {
  'c-exp': {
    by: 'zhaoyutong',
    at: dayAt(1, 17, 12),
    html: `<h3>评测口径</h3>
<p>检索统一报 R@1 / R@5 / R@10，表里默认写文→图。Flickr30K 用 1K 测试集，COCO 用 5K，不用 1K。细节见 <code>docs/评测口径说明.md</code>。</p>
<h3>待办</h3>
<ul data-type="taskList"><li data-type="taskItem" data-checked="true"><p>对齐 Flickr30K 1K 的测试集划分</p></li><li data-type="taskItem" data-checked="false"><p>三份评测脚本合成一份</p></li><li data-type="taskItem" data-checked="false"><p>长描述截断实验（陈思远）</p></li><li data-type="taskItem" data-checked="false"><p>中期报告的数据一节（赵雨桐）</p></li></ul>`,
  },
  'c-papers': {
    by: 'wanglei',
    at: dayAt(2, 20, 40),
    html: `<h3>读的顺序</h3>
<ol><li><p>CLIP（读完，笔记在 <code>docs/papers/CLIP-精读.md</code>）</p></li><li><p>BLIP（这周）</p></li><li><p>ALBEF</p></li><li><p>BLIP-2</p></li></ol>
<p>每篇读完把笔记放进 <code>docs/papers</code>，周五组会讲 10 分钟。</p>`,
  },
  'c-meeting': {
    by: 'linxiao',
    at: dayAt(1, 18, 20),
    html: `<h3>组会</h3>
<p>每周五下午，地点见频道最新消息。每人 10 分钟：做了什么、数字、下周做什么。</p>
<h3>轮值</h3>
<ul><li><p>本周：王磊（BLIP 基线）</p></li><li><p>下周：陈思远（截断实验）</p></li></ul>`,
  },
  'c-general': {
    by: 'zhouke',
    at: dayAt(1, 9, 50),
    html: `<h3>机器</h3>
<ul><li><p>A100 × 1：数据在 <code>/data</code>，新硬盘在 <code>/data2</code></p></li><li><p>跑长任务前在这里说一声</p></li></ul>`,
  },
}

// ---- 芝士整理 ----

export type SummaryKind = 'decided' | 'found' | 'open'

export const SUMMARY_LABELS: Record<SummaryKind, string> = {
  decided: '定了',
  found: '发现',
  open: '没定',
}

/** One sentence of 芝士's part of a channel's document, pointing at the message it rests on. */
export interface SummaryItem {
  kind: SummaryKind
  line: string
  text: string
}

/** 芝士's part of each channel's document, as of the message `upto` (later messages are not in it). */
export const SUMMARY: Record<string, { upto: string; items: SummaryItem[] }> = {
  'c-exp': {
    upto: 'e7',
    items: [
      { kind: 'decided', line: 'e4', text: '评测统一用 `eval/retrieval.py`，COCO 用 5K 口径' },
      {
        kind: 'found',
        line: 'e5',
        text: '和论文差 1.1 个点，可能是 `eval/preprocess.py` 只做了 Resize(256)、没有 center crop',
      },
      { kind: 'open', line: 'e6', text: '手上有三份评测脚本，合成哪一份还没人认领' },
    ],
  },
  'c-papers': {
    upto: 'p4',
    items: [
      { kind: 'decided', line: 'p3', text: '和 BLIP 表 5 对数时，COCO 按 5K 测试集比，不和 1K 混' },
      {
        kind: 'found',
        line: 'p1',
        text: 'BLIP 笔记在 `docs/papers/BLIP-精读.md`：要点是 CapFilt 洗数据，文本端一份参数三种用法',
      },
      { kind: 'open', line: 'p4', text: 'ITM 的 hard negative 按相似度从 batch 里采，我们复现时要不要照做，还没人回' },
    ],
  },
  'c-meeting': {
    upto: 'g3',
    items: [
      { kind: 'decided', line: 'g3', text: '周五组会改到下午 3 点，在 302' },
      { kind: 'found', line: 'g2', text: '周珂周五请假，回来看纪要' },
    ],
  },
  'c-general': {
    upto: 'z4',
    items: [
      { kind: 'decided', line: 'z1', text: 'A100 周三晚 10 点停机维护约两小时，之前存好 checkpoint' },
      { kind: 'found', line: 'z3', text: '新的 4T 硬盘挂在 `/data2`，COCO 原图已经拷过去了' },
      { kind: 'open', line: 'z4', text: '中期报告下周三交，数据那一节还等赵雨桐补' },
    ],
  },
}
