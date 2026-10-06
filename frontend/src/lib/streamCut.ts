// 答案的流在答案说完之前断了，而且接着读也读不下去（`followEventStream` 抛它）。
//
// 它放在这儿，而不是抛它的那个接口文件（`api/eventStream.ts`）旁边，是因为接住它的
// 那一层不许够得着接口层：`composables/useDocAgent.ts` 喂的是文档面板，而
// `components/panels/**` 下面每个组件都是「场景」，场景只吃 props 和事件。这个类是
// 一个信号，不是一次取数，所以它属于这里。
export class StreamCut extends Error {
  constructor() {
    super('the answer stopped arriving')
  }
}
