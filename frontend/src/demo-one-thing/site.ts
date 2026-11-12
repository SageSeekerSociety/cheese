// What 芝士 does in 演示一's task, step by step along its plan: what it
// thinks, reads, runs (and what that printed) and says. The task's 文件
// shows the runs of the steps done so far (state.liveStep).
export type SiteStep =
  | { kind: 'thinking'; text: string }
  | { kind: 'bash'; command: string; description: string; output: string; failed?: boolean }
  | { kind: 'read'; path: string; output: string }
  | { kind: 'text'; text: string }

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
