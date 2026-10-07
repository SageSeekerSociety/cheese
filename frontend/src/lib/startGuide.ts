// 新用户那条起步路上有两件东西看同一份进度：项目本体里那张「开始清单」卡（四步），
// 和一路指着按钮的气泡浮层（五步）。这份文件只写「哪一步算做完了」，不碰网络也不碰
// 界面——卡和浮层各自把手上那份事实喂进来，同一个事实必须得出同一个答案。
//
// 五步里只有第 1 步「建项目」发生在项目外面，所以卡里没有它：卡画在项目本体上，
// 到那儿的时候项目早就有了。

export type StartStepKey = 'project' | 'talk' | 'materials' | 'repo' | 'people'

/** 气泡浮层一步一亮，按这个顺序。 */
export const GUIDE_STEPS: readonly StartStepKey[] = ['project', 'talk', 'materials', 'repo', 'people']

/** 清单卡画项目里那四步。 */
export const CARD_STEPS: readonly StartStepKey[] = ['talk', 'materials', 'repo', 'people']

/** 每一步「做没做」要看的那几个事实。没问到的那两项按「还没做」算。 */
export interface StartFacts {
  /** 这个人已经有项目了：自己建的，或者用邀请码进了别人的。 */
  hasProject: boolean
  /** 芝士在这个房间里开过口（人跟它说上话了吗）。 */
  agentHasSpoken: boolean
  /** 这个人往这个房间里放过附件。 */
  roomHasAttachment: boolean
  /** 项目资料库里有几份东西；还没问到是 null。 */
  libraryCount: number | null
  /** 代码仓库接上了没有；还没问到是 null。 */
  forgeConnected: boolean | null
  /** 名册上有第二个人（除掉自己和 AI 队友）。 */
  othersInProject: boolean
}

export function stepDone(key: StartStepKey, facts: StartFacts): boolean {
  switch (key) {
    case 'project':
      return facts.hasProject
    case 'talk':
      return facts.agentHasSpoken
    // 材料两条路都算数：直接拖进这个房间，或者放进项目资料库。房间里那份本地就
    // 知道，所以只在「房间里还没有附件」时才去问资料库——大部分人是在房间里给的。
    case 'materials':
      return facts.roomHasAttachment || (facts.libraryCount ?? 0) > 0
    case 'repo':
      // 问不到（null）时不算做完：这一步说错了，人白跑一趟；说少了只是多提示一次。
      return facts.forgeConnected === true
    case 'people':
      return facts.othersInProject
  }
}

/**
 * 按 `steps` 给的顺序找出第一件还没做的；都做完了返回 null。
 *
 * 气泡浮层就把这个结果当作「现在该亮哪一步」：用户真去做了，事实一变，答案自己
 * 往前走一步，不需要任何「下一步」的按钮或定时器。
 */
export function firstPendingStep(steps: readonly StartStepKey[], facts: StartFacts): StartStepKey | null {
  for (const key of steps) {
    if (!stepDone(key, facts)) return key
  }
  return null
}
