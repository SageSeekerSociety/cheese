// 测试里不许真发网络请求。四条出口全堵上，每条都指出是哪个用例、要去哪里。
//
// 这不是「fetch 的替身」，是四条出口各自的陷阱，因为一个请求绕开 `globalThis.fetch`
// 有好几条路：
//
// - `XMLHttpRequest`：happy-dom 自己实现了一套，直接开真连接，从不经过页面的 fetch
//   （上传进度 `lib/xhrUpload.ts` 走的就是它）。它不能像另外三条那样整个换成替身：
//   接口契约测试（`src/api/*Contract.spec.ts`）故意用真的 XHR 配 msw，而 msw 的
//   XHR 拦截器是把它当时在全局上看到的那个 XHR 包一层代理 —— 换成替身，它包的还是
//   替身，连被 mock 的请求都发不出去。所以这条做成「真 XHR 的子类，只在 send 上拦」：
//   msw 接过去的请求根本不会走到 send，走得到的就是没被拦下的那一次。
// - `WebSocket`：happy-dom 没有这个接口，于是 Node 22 内建的全局 WebSocket 露在测试
//   里，`new WebSocket(...)` 就是一条真 TCP 连接（房间连接 `lib/roomLink.ts`）；
// - `navigator.sendBeacon`：happy-dom 把它实现成「拿页面自己的 fetch 发一个 POST」，
//   所以它既绕过 `globalThis.fetch`，又能在没人接的时候把失败留在那里
//   （`errorReporter.ts` 在 pagehide 里上报就是它）；
// - `fetch`：本来就有陷阱，留着。
//
// 没有陷阱的时候，未 mock 的请求会去连 happy-dom 的默认地址 —— `http://localhost:3000`，
// 正好是前端 dev server 的端口 —— 于是测试要么拿到一个 ECONNREFUSED（CI 上就是这样：
// 一条 `at createConnectionError (node:net:…)` 的栈，地址 127.0.0.1:3000），要么连上
// 了本地真在跑的服务，两者都不是这个用例该依赖的东西。陷阱让它当场失败并点名。

/** 已经出现过、还没被核对过的请求。没有面向上层导出的意图，只有守卫自己的用例要用。 */
const unexpectedRequests: string[] = []

/** 每个真的 XHR 实例开的那个地址（`open(method, url)`），send 时才拿得到。 */
const xhrTargets = new WeakMap<XMLHttpRequest, string>()

function targetOf(args: unknown[]): string {
  const first = args[0]
  if (typeof first === 'string') return first
  if (first instanceof URL) return first.href
  if (first && typeof first === 'object' && 'url' in first) return String((first as { url: unknown }).url)
  return String(first)
}

function currentTest(): string {
  return expect.getState().currentTestName ?? '<unknown test>'
}

function complain(api: string, target: string): never {
  const where = `"${currentTest()}"`
  throw new Error(
    `Unexpected ${api} in unit test: ${target} (in ${where}). ` +
      'Mock every network call used by this test; see src/test/networkTrap.ts.'
  )
}

/** 一次真请求的尝试。记下来（用例把异常吞掉时 afterEach 仍然要失败），然后当场报错。 */
function note(api: string, target: string): never {
  unexpectedRequests.push(`${api} ${target}`)
  complain(api, target)
}

/** 一个「一动手就是一次真请求」的构造器替身：new WebSocket 就是。 */
function trapConstructor(api: string) {
  const Stub = function (this: unknown, ...args: unknown[]) {
    note(api, targetOf(args))
  }
  // 代码可能读这些静态量（CONNECTING/OPEN…），保留它们。
  return Stub as unknown as new (...args: unknown[]) => object
}

/** 真的 XHR 的子类，只把 send 换成陷阱（为什么不能整个换掉见文件开头）。 */
function trapXMLHttpRequest() {
  const Native = globalThis.XMLHttpRequest

  class TrapXMLHttpRequest extends Native {
    open(method: string, url: string | URL, ...rest: unknown[]): void {
      // 记下地址，send 时才报得出「要去哪里」；happy-dom 的默认地址就是从这里冒出来的。
      xhrTargets.set(this, `${method} ${String(url)}`)
      return (super.open as (...args: unknown[]) => void)(method, url, ...rest)
    }

    send(...args: unknown[]): void {
      void args
      note('XMLHttpRequest', xhrTargets.get(this) ?? '<never opened>')
    }
  }

  // readyState 那组静态量从真的 XHR 上继承下来就够（它们本来就挂在类上）；只在
  // 子类拿不到时才补，直接赋值会撞上父类那些只读属性（Object.assign 走 [[Set]]）。
  Object.entries({ UNSENT: 0, OPENED: 1, HEADERS_RECEIVED: 2, LOADING: 3, DONE: 4 }).forEach(([name, value]) => {
    if (TrapXMLHttpRequest[name as 'DONE'] === undefined) {
      Object.defineProperty(TrapXMLHttpRequest, name, { value, configurable: true })
    }
  })
  return TrapXMLHttpRequest as unknown as typeof XMLHttpRequest
}

/** 装上四条陷阱。每个测试文件都会在 setup 里跑一次。 */
export function installNetworkTrap(): void {
  globalThis.fetch = async (input) => {
    // 走 note()，和另外三条出口一样：报错里带用例名，抛出的 Error 在 async 函数里
    // 变成 rejected promise，调用方 await 得到的就是它。
    note('fetch', targetOf([input]))
  }

  globalThis.XMLHttpRequest = trapXMLHttpRequest()

  const ws = trapConstructor('WebSocket')
  Object.assign(ws, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 })
  globalThis.WebSocket = ws as unknown as typeof WebSocket

  const beacon = (url: unknown) => note('navigator.sendBeacon', targetOf([url]))
  // navigator 在 happy-dom 里是全局的那个实例，改它就是改页面看到的那个。
  if (typeof navigator !== 'undefined') navigator.sendBeacon = beacon as Navigator['sendBeacon']
}

/** 取走并清空记录。守卫自己的用例用它收尾，别处不用。 */
export function takeUnexpectedRequests(): string[] {
  return unexpectedRequests.splice(0)
}
