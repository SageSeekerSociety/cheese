// 动效台的清单：产品里每一种变化各摆一格，写明它用的是哪几条 token。
//
// 这一份**不进 catalog**，也不被 catalog 引用。`catalog.ts` 是「有哪个组件、每一格
// 喂什么」的注册表，收录闸和逐格挂载测试盯着它；动效台讲的是「变化怎么发生」，不是
// 「哪个组件长什么样」，混进注册表只会让那两道闸去管一件不该它们管的事。两份数据并
// 排放在同一个目录里，但谁也不认识谁。
//
// `tokens` 里写的名字必须是 `src/style.css` 的 `:root` 里真有的：`demoTransitions.spec.ts`
// 会拿那一份文件逐个核对。token 改名时，这里跟着改，忘了就会红。

/** 一格小样。`MotionSample.vue` 按这个名画对应的那一件。 */
export type TransitionId = 'expand' | 'dialog' | 'drawer' | 'tabs' | 'row' | 'ripple' | 'sheet' | 'skeleton'

export interface TransitionEntry {
  id: TransitionId
  /** 这一格在讲什么变化。 */
  name: string
  /** 这个变化在产品里出现在哪、为什么是这个时长。 */
  about: string
  /** 它用的 token，名字照 `src/style.css` 写。 */
  tokens: string[]
}

export const TRANSITIONS: ReadonlyArray<TransitionEntry> = [
  {
    id: 'expand',
    name: '卡片展开',
    about: '点一下标题，内容从收着到展开；高度和透明度一起走。',
    tokens: ['--dur-base', '--ease-out'],
  },
  {
    id: 'dialog',
    name: '浮层出现',
    about: '遮罩先淡进来，对话框从 92% 放大到 100%——从中间长出来，不是从边上滑进来。',
    tokens: ['--dur-base', '--ease-out'],
  },
  {
    id: 'drawer',
    name: '面板收放',
    about: '工作面板从右边推进来；离开时快一档，先走完再缩对话。',
    tokens: ['--dur-slow', '--ease-out', '--dur-quick', '--ease-in'],
  },
  {
    id: 'tabs',
    name: '页签指示条',
    about: '指示条在同一条轨道上滑过去，不淡出再淡入——它一直是同一个东西。',
    tokens: ['--dur-base', '--ease-standard'],
  },
  {
    id: 'row',
    name: '新消息进入',
    about: '新的一行从下方 8px 淡进来，落位就停；已有的行不动。',
    tokens: ['--dur-base', '--ease-out'],
  },
  {
    id: 'ripple',
    name: '按下回执',
    about: '按下的那一刻就给回执，不等松手——发送键沉到 92% 就是这一下。',
    tokens: ['--dur-press', '--ease-standard'],
  },
  {
    id: 'sheet',
    name: '手机底部弹层',
    about: '手机上从底部升起来，停下时贴住安全区；点遮罩按原路回去。',
    tokens: ['--dur-slow', '--ease-out'],
  },
  {
    id: 'skeleton',
    name: '骨架到内容',
    about: '骨架先占住要用的高度，内容到了原地换上，版面不跳。这一格在等数据时一直循环，不是走一遍就停。',
    tokens: ['--dur-slow'],
  },
]
