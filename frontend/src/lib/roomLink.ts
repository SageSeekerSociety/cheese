// 一个页面一条房间连接：看着的每个房间都在这一条上订阅、退订。
//
// 以前每个房间各开一条 WebSocket。换一次频道就是关掉一条、再开一条：到香港的边缘重新
// 握手，过一趟隧道，再认一次人，这期间房间是断着的。现在换频道只是在已经连着的这条上
// 说两句话（退订上一个，订阅这一个），没有新的握手。
//
// 房间那一层（`useRoomSocket`）拿到的「房间通道」长得和一条 WebSocket 一样：
// `onopen` / `onmessage` / `onclose` / `onerror`、`send`、`close`、`readyState`。它
// 的重连、心跳、拒绝之后不再重试，都照旧按房间做；这里只管把房间的帧放到同一条
// 连接上，再按房间分回去。
//
// - 订阅帧带着此刻的 token：连接可以一直开着，而 token 中途会换；一个房间该按页面现在
//   拿着的身份来认，不是连上那一刻的。
// - 服务端回 `subscribed` 才算这个房间连上（`onopen`）：那之后房间里落下的每一帧它都
//   听得见，这之前发的消息的回显会丢。它还说了订阅生效那一刻房间里最后存进来的那一条
//   的编号（`newest`），比它晚的都会推过来；手里没到那个号的房间要自己补读一次。还带着
//   那一刻房间的样子（`room`，见 query/snapshot）：之后来的帧都比它新。
// - 服务端回 `closed`（拒绝、读得太慢被断开）只结束这一个房间。
// - 连接本身断了，每个房间都收到一次 `onclose`，各自按自己的退避重新订阅；第一个回来
//   的订阅把连接重新打开。
// - 一个房间的心跳没有回音（`drop`），说明这条连接已经不通了：整条换掉，别的房间也跟着
//   重新订阅 —— 在一条不通的连接上重新订阅，回音永远不会来。
// - 一个房间同一时刻只有一个通道：再开一次就顶掉前一个。一个页面上同时开着的对话栏看的
//   都是不同的 id（频道自己、它的一条支线、一个任务），所以不会互相顶；哪天要让两处同时
//   看同一个房间，这里得改成几处共用一个订阅。
import type { RoomSnapshot } from '@/query/snapshot'

import { authToken, BASE } from '@/api/http'

const OPEN = 1
const CLOSED = 3

export interface RoomChannel {
  readyState: number
  /** 订阅生效那一刻房间里最后存进来的那一条的编号（`Block.seq`）；`null` 是房间还空着，`undefined` 是没说。 */
  newest?: number | null
  /** 订阅生效那一刻房间的样子；`null` 是这一次没读成。 */
  room?: RoomSnapshot | null
  onopen: (() => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
  send(data: string): void
  /** 不再看这个房间。 */
  close(): void
  /** 这个房间的心跳没有回音：连接已经不通，整条换掉。 */
  drop(): void
}

interface Frame {
  type?: string
  topic?: string
  [key: string]: unknown
}

function linkUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  // 浏览器不能给 WebSocket 设 Authorization 头，token 只能放在 ?token= 里。
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  // BASE，不手写 '/api'：网关正好剥掉一个 '/api'，手写一份就会对不上路由。
  return `${proto}://${window.location.host}${BASE}/rooms/live${q}`
}

class Channel implements RoomChannel {
  readyState = 0
  newest?: number | null
  room?: RoomSnapshot | null
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(
    readonly topic: string,
    private readonly link: Link
  ) {}

  send(data: string): void {
    if (this.readyState !== OPEN) return
    this.link.send({ ...(JSON.parse(data) as Frame), topic: this.topic })
  }

  close(): void {
    this.link.leave(this)
  }

  drop(): void {
    this.link.reset()
  }

  /** 这个房间结束了（被拒、被断开、连接断了）：说一次，之后不再收任何帧。 */
  ended(withError: boolean): void {
    if (this.readyState === CLOSED) return
    this.readyState = CLOSED
    if (withError) this.onerror?.()
    this.onclose?.()
  }
}

class Link {
  private socket: WebSocket | null = null
  private ready = false
  private readonly rooms = new Map<string, Channel>()

  open(topic: string): Channel {
    this.rooms.get(topic)?.ended(false)
    const room = new Channel(topic, this)
    this.rooms.set(topic, room)
    if (!this.socket) this.connect()
    else if (this.ready) this.subscribe(room)
    return room
  }

  leave(room: Channel): void {
    room.readyState = CLOSED
    if (this.rooms.get(room.topic) !== room) return
    this.rooms.delete(room.topic)
    if (this.ready) this.send({ type: 'unsubscribe', topic: room.topic })
  }

  send(frame: Frame): void {
    if (this.ready) this.socket?.send(JSON.stringify(frame))
  }

  /** 关掉这条连接；看着的每个房间都会收到一次 `onclose`，再各自回来订阅。 */
  reset(): void {
    const socket = this.socket
    if (!socket) return
    this.forget(socket)
    socket.close()
    this.endAll(true)
  }

  private subscribe(room: Channel): void {
    this.send({ type: 'subscribe', topic: room.topic, token: authToken() })
  }

  private connect(): void {
    const socket = new WebSocket(linkUrl())
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.ready = true
      for (const room of this.rooms.values()) this.subscribe(room)
    }
    socket.onmessage = (event: MessageEvent) => {
      if (this.socket !== socket) return
      let frame: Frame
      try {
        frame = JSON.parse(event.data as string) as Frame
      } catch {
        return
      }
      this.deliver(frame)
    }
    socket.onerror = () => {
      if (this.socket === socket) for (const room of this.rooms.values()) room.onerror?.()
    }
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.forget(socket)
      this.endAll(false)
    }
  }

  private deliver(frame: Frame): void {
    const { topic, ...rest } = frame
    // 没有房间的帧说的是这条连接本身（连上时认不出人）：每个房间都该知道。
    const rooms = topic === undefined ? [...this.rooms.values()] : [this.rooms.get(topic)].filter(Boolean)
    for (const room of rooms as Channel[]) {
      if (rest.type === 'subscribed') {
        room.readyState = OPEN
        room.newest = typeof rest.newest === 'number' || rest.newest === null ? rest.newest : undefined
        room.room = (rest.room as RoomSnapshot | null | undefined) ?? null
        room.onopen?.()
      } else if (rest.type === 'closed') {
        this.rooms.delete(room.topic)
        room.ended(false)
      } else {
        room.onmessage?.({ data: JSON.stringify(rest) })
      }
    }
  }

  private forget(socket: WebSocket): void {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    if (this.socket === socket) {
      this.socket = null
      this.ready = false
    }
  }

  private endAll(withError: boolean): void {
    const rooms = [...this.rooms.values()]
    this.rooms.clear()
    for (const room of rooms) room.ended(withError)
  }
}

let link = new Link()

/** 在这个页面的房间连接上看着 `topic`。 */
export function openRoomChannel(topic: string): RoomChannel {
  return link.open(topic)
}

/** 换一个人登录，或测试之间：上一条连接和它上面的房间都不要了。 */
export function resetRoomLink(): void {
  link.reset()
  link = new Link()
}
