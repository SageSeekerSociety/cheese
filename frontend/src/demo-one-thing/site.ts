// What 芝士 did, as the records the product's 现场 (PanelSite) reads: one
// block per native record, with the parts the panel draws (thinking, a tool
// call, its output, a line of text). Shaped like the product's own sample
// records in views/demo/catalogPanelsFixtures.
import type { Block } from '@/cx_types'

export type SiteStep =
  | { kind: 'thinking'; text: string }
  | { kind: 'bash'; command: string; description: string; output: string; failed?: boolean }
  | { kind: 'read'; path: string; output: string }
  | { kind: 'text'; text: string }

function hash(seed: string): string {
  let h = 2166136261
  let out = ''
  for (let n = 0; n < 24; n++) {
    for (const c of seed + n) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
    out += 'abcdefghijklmnopqrstuvwxyz0123456789'[(h >>> 0) % 36]
  }
  return out
}

export function siteBlocks(thing: string, steps: SiteStep[], startIso: string): Block[] {
  const start = Date.parse(startIso)
  const out: Block[] = []
  let seq = 0
  const push = (record: unknown, parts: unknown[], tool?: string) => {
    seq++
    out.push({
      id: `${thing}-rec-${seq}`,
      conversation_id: thing,
      kind: 'event',
      author_type: 'participant',
      author: 'cheese',
      content: '',
      turn_id: `${thing}-turn`,
      created_at: new Date(start + seq * 17_000).toISOString(),
      meta: { record, sequence: seq, in_room: false, tool, parts },
    } as Block)
  }
  steps.forEach((s, i) => {
    // Call ids read like the runtime's own (call_00_<random>), not like a fixture's.
    const call = `call_${String(i).padStart(2, '0')}_${hash(`${thing}${i}`)}`
    if (s.kind === 'thinking') {
      push({ type: 'assistant', message: { content: [{ type: 'thinking', thinking: s.text }] } }, [
        { kind: 'thinking', text: s.text },
      ])
    } else if (s.kind === 'text') {
      push({ type: 'assistant', message: { content: [{ type: 'text', text: s.text }] } }, [
        { kind: 'text', text: s.text },
      ])
    } else {
      const name = s.kind === 'bash' ? 'Bash' : 'Read'
      const input = s.kind === 'bash' ? { command: s.command, description: s.description } : { file_path: s.path }
      const preview = s.kind === 'bash' ? s.description : s.path
      push(
        { type: 'assistant', message: { content: [{ type: 'tool_use', id: call, name, input }] } },
        [{ kind: 'tool', name, call_id: call, input, preview }],
        name
      )
      push({ type: 'user', message: { content: [{ type: 'tool_result', tool_use_id: call, content: s.output }] } }, [
        { kind: 'output', call_id: call, text: s.output, failed: s.kind === 'bash' && !!s.failed },
      ])
    }
  })
  return out
}

/** The scripted task's run, in the order the checklist ticks: `upTo` steps of the plan done. */
export const T4_STEPS: SiteStep[][] = [
  [
    { kind: 'thinking', text: '先看两份预处理哪里不一样：项目里的 eval/preprocess.py 和王磊 notebook 里那份。' },
    {
      kind: 'read',
      path: 'eval/preprocess.py',
      output:
        'import torchvision.transforms as T\n\n\ndef preprocess(img):\n    return T.Compose([\n        T.Resize(256),\n        T.ToTensor(),\n    ])(img)',
    },
    {
      kind: 'bash',
      command: "grep -n 'Resize\\|CenterCrop' notebooks/wanglei_eval.ipynb",
      description: '看王磊的预处理',
      output: '41:    "    T.Resize(224, interpolation=BICUBIC),\\n",\n42:    "    T.CenterCrop(224),\\n",',
    },
  ],
  [
    { kind: 'text', text: '项目里那份少了 center crop、插值也不对，按论文改成 224 + BICUBIC + center crop。' },
    {
      kind: 'bash',
      command: 'python -m pytest tests/test_eval.py -q',
      description: '跑评测脚本的单测',
      output: '......\n6 passed in 1.84s',
    },
  ],
  [
    {
      kind: 'bash',
      command: 'python -m eval.retrieval --data flickr30k --split 1k',
      description: '跑 Flickr30K 1K',
      output: 'encoding 1000 images, 5000 captions … done (38s)\nR@1 79.1  R@5 94.8  R@10 97.6',
    },
  ],
  [
    {
      kind: 'bash',
      command: 'python -m eval.retrieval --data coco --split 5k',
      description: '跑 COCO 5K',
      output: 'encoding 5000 images, 25010 captions … done (3m12s)\nR@1 50.3  R@5 74.9  R@10 83.2',
    },
  ],
  [{ kind: 'text', text: '结果表写进 results/eval_results.md：Flickr30K R@1 79.1，和论文的 79.3 基本对上。' }],
]
