/**
 * 上次在某个项目里待的那一页：项目里任何一页都算，不只是频道和任务。
 *
 * `path` 是 `/projects/<项目>` 之后的那一段（`tasks/318`、`overview`、`members/lisi`；
 * 项目首页是空串）。项目用短名，所以这一段里不带它——项目改了短名，回来时按现在的
 * 短名重新拼，地址照旧打得开。
 */
export interface RememberedPlace {
  path: string
  /**
   * 这一页所在的频道；不在频道里的时候是 null。
   *
   * 频道从清单里消失时这一份就作废（`stores/workspace/queries.ts` 里校验的就是它）：
   * 任务页借频道的外框和名册，频道没了，那个地址已经打不开。
   */
  room: string | null
}

/** 存在浏览器里的那一份布局偏好：可能是老版本写的，也可能被人手改过。 */
export interface StoredLayout {
  chatPct?: number
  /** 右侧面板开着还是收着，这个人自己选过一次之后就照它来；没选过按宽度定。 */
  panelOpen?: boolean
  lastProjectId?: string
  /** 每个项目上次待的那一页：回到那个项目时，rail 那一格直接落回它。 */
  lastPlaceByProject?: Record<string, RememberedPlace>
}
