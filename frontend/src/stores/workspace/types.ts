/**
 * 上次在某个项目里待的地方：一个任务页，或者一个频道。
 *
 * `room` 是它所在的频道。记着的是任务时，任务本身在频道清单里看不见（任务是另一份
 * 清单，很沉，rail 不为它多发一次请求），但频道不见了就说明这个地方已经打不开——
 * 所以任务的这一种要连频道一起记，读频道清单时校验的就是它。
 */
export interface RememberedPlace {
  kind: 'task' | 'channel'
  id: string
  room: string
}

/** 存在浏览器里的那一份布局偏好：可能是老版本写的，也可能被人手改过。 */
export interface StoredLayout {
  chatPct?: number
  /** 右侧面板开着还是收着，这个人自己选过一次之后就照它来；没选过按宽度定。 */
  panelOpen?: boolean
  lastProjectId?: string
  /** 每个项目上次待的地方：回到那个项目时，rail 那一格直接落回它。 */
  lastPlaceByProject?: Record<string, RememberedPlace>
}
