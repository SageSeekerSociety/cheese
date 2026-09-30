/**
 * 提问预览用的**假服务**：假装它是 `POST /topics/blocks/{id}/answer`。
 *
 * **这不是真实产品链路。** 产品现在还没有「可作答的提问」这条链路，所以「失败 →
 * 输入还在 → 重试成功」只能在这里一来一回地走通。放一个真的服务对象进来，而不是
 * 让组件自己 `if (failNextSubmit)` 假装失败，是因为后者压根没走到任何服务 —— 那
 * 样测出来的是组件的分支，不是「交上去被拒了会怎样」。
 *
 * **真实契约对照**（`backend/app/api/routes/topics.py` 的 `answer_options`）：
 *
 * - 收的是 `option` + `author`，`option` 必须**逐字**在 `meta.options` 里，差一个
 *   字就是 400「不在选项里」。
 * - **没有备注字段。** 自由输入现在没有落库的地方，只随消息正文走（那个端点把你
 *   选的那句话作为消息发出去，`meta.answered` 只记 `option`）。
 * - 已答过的题再交是 400「已由 xxx 选过」。所以**更正回答在当前产品里做不到**，
 *   是本次的新增目标，不是把 400 当 bug 抹掉。
 *
 * 也就是说：这个假服务吐出的 `{id, option, note}` 是**拟实现**的载荷，不是现在后端
 * 认的那一个。`note` 是拟新增字段，`option` 那一段按现契约必须落在 `meta.options`
 * 之内 —— 所以「以上都不是」这类客户端自动补的拒绝项，现在过不了这个端点，得连后端
 * 一起改。
 */
export interface FakeAnswerPayload {
  /** 题号。真实端点的路径参数是 block id。 */
  id: string
  /** 被选中那一项的 label —— 现契约里这一串要逐字命中 `meta.options`。 */
  option: string
  /** 自由输入。**拟新增字段**，现端点收不到。 */
  note: string
}

export interface FakeAnswerService {
  /** 每一次真的走到服务的载荷，按顺序。失败的那几次也在里面。 */
  log: FakeAnswerPayload[]
  /** 一共被调了几次（含被拒的）。 */
  calls: () => number
  submit: (payload: FakeAnswerPayload) => Promise<void>
}

/**
 * @param failFirst 前几次真的拒掉，之后放行。给 1 就是「失败一次 → 重试成功」。
 */
export function fakeAnswerService(opts: { failFirst?: number } = {}): FakeAnswerService {
  const failFirst = opts.failFirst ?? 1
  const log: FakeAnswerPayload[] = []
  let calls = 0
  return {
    log,
    calls: () => calls,
    async submit(payload: FakeAnswerPayload) {
      calls += 1
      // 被拒的也记下来：能证明「失败那一次确实打到过服务」，不是组件自己编的。
      log.push(payload)
      if (calls <= failFirst) {
        throw new Error('交上去没成功：假服务按脚本拒了这一次')
      }
    },
  }
}
