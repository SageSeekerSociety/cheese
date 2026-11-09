// The demo's sample project: who is in it, what the channels say, the task's
// changes and the official versions of the files. All of it is made up; the
// screens that draw it are the product's own components.
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
  { handle: 'cheese', name: '芝士', agent: true },
]

export const NAMES: Record<string, string> = Object.fromEntries(PEOPLE.map((p) => [p.handle, p.name]))

export const CHANNELS = [
  { id: 'c-general', title: '综合', about: '项目里的大小事' },
  { id: 'c-papers', title: '论文精读', about: '每周一篇，读完写笔记' },
  { id: 'c-exp', title: '实验记录', about: '实验结果与评测口径' },
  { id: 'c-meeting', title: '组会', about: '组会安排和汇报材料' },
]

export const PROJECT = { team: '全栈开发小队', name: '多模态检索论文复现' }

export const ASK = '<@cheese> 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10'
/** What the composer shows while the ask is being typed: the token drawn as its name. */
export const ASK_TYPED = '@芝士 把大家的评测脚本统一一下，固定输出 R@1 / R@5 / R@10'

export const PLAN = [
  '对齐图像预处理（224，center crop）',
  '统一 eval/retrieval.py',
  '跑 Flickr30K 1K',
  '跑 COCO 5K',
  '整理结果表',
]

export const TERM = [
  '$ python -m eval.retrieval --data flickr30k --split test',
  'R@1 79.1  R@5 94.8  R@10 97.6',
  '$ python -m eval.retrieval --data coco --split 5k',
  'R@1 50.3  R@5 74.9  R@10 83.2',
]

export const RESULTS_MD = `| 测试集 | R@1 | R@5 | R@10 |
| --- | --- | --- | --- |
| Flickr30K 1K | 79.1 | 94.8 | 97.6 |
| COCO 5K | 50.3 | 74.9 | 83.2 |

统一预处理后 Flickr30K R@1 = 79.1，和论文的 79.3 基本对上。`

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
function asContext(path: string, lines: string[]) {
  return parseDiffLines(
    [`diff --git a/${path} b/${path}`, `--- a/${path}`, `+++ b/${path}`, `@@ -1,${lines.length} +1,${lines.length} @@`]
      .concat(lines.map((l) => ` ${l}`))
      .join('\n')
  )
}

export function preprocessFile(accepted: boolean) {
  return asContext(
    'eval/preprocess.py',
    accepted
      ? [
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
      : [
          'import torchvision.transforms as T',
          '',
          '',
          'def preprocess(img):',
          '    return T.Compose([',
          '        T.Resize(256),',
          '        T.ToTensor(),',
          '    ])(img)',
        ]
  )
}

export function retrievalFile(accepted: boolean) {
  const head = [
    'import torch',
    '',
    'from eval.preprocess import preprocess',
    '',
    '',
    'def evaluate(model, loader):',
    '    image_feats, text_feats = encode_all(model, loader)',
    '    sims = image_feats @ text_feats.T',
  ]
  return asContext(
    'eval/retrieval.py',
    accepted
      ? head.concat([
          '    return {',
          '        "R@1": recall_at_k(sims, 1),',
          '        "R@5": recall_at_k(sims, 5),',
          '        "R@10": recall_at_k(sims, 10),',
          '    }',
        ])
      : head.concat(['    return recall_at_1(sims)'])
  )
}

export interface FileEntry {
  group: '文档' | '代码' | '成果'
  path: string
  kind: 'doc' | 'word' | 'code' | 'result'
}

export const FILES: FileEntry[] = [
  { group: '文档', path: 'docs/组会纪要-11-07.md', kind: 'doc' },
  { group: '文档', path: 'docs/评测口径说明.md', kind: 'doc' },
  { group: '文档', path: 'report/中期报告.docx', kind: 'word' },
  { group: '代码', path: 'eval/retrieval.py', kind: 'code' },
  { group: '代码', path: 'eval/preprocess.py', kind: 'code' },
  { group: '成果', path: 'results/eval_results.md', kind: 'result' },
]

export const MINUTES_DOC = 'docs/组会纪要-11-07.md'

/** 组会纪要 as HTML: item 3 carries 芝士's pending suggestion as the editor's own suggestion marks. */
export function minutesHtml(accepted: boolean): string {
  const third = accepted
    ? '下周组会前把 COCO 5K 的结果更新到这里。'
    : '<del data-suggestion="cheese:m3">（待补）</del><ins data-suggestion="cheese:m3">下周组会前把 COCO 5K 的结果更新到这里。</ins>'
  return `<h2>组会纪要 11-07</h2>
<ol>
<li><p>评测口径：统一用 <code>eval/retrieval.py</code>，COCO 用 5K 测试集。</p></li>
<li><p>下周：王磊复现 BLIP 基线，陈思远补长描述截断实验。</p></li>
<li><p>${third}</p></li>
<li><p></p></li>
</ol>`
}

export const CRITERIA_DOC_HTML = `<h2>评测口径说明</h2>
<p>检索评测统一输出 R@1 / R@5 / R@10。</p>
<ul><li><p>Flickr30K 用 1K 测试集。</p></li><li><p>COCO 用 5K 测试集，不用 1K。</p></li><li><p>图像预处理：224，BICUBIC，center crop。</p></li></ul>`

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
  ],
}
