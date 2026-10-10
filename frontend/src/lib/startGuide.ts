// 新用户那条起步路上的几步：项目本体里那张「开始清单」卡（四步）和一路指着按钮的
// 气泡浮层（五步）。哪一步算做完了，由 `composables/useGettingStarted.ts` 一处推出来，
// 卡和浮层读的是同一份。
//
// 五步里只有第 1 步「建项目」发生在项目外面，所以卡里没有它：卡画在项目本体上，
// 到那儿的时候项目早就有了。

export type StartStepKey = 'project' | 'talk' | 'materials' | 'repo' | 'people'
