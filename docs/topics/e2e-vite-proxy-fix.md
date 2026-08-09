## 状态：已修复，待验收

## 改动

`<&frontend/vite.config.ts>` 的 dev server proxy：

1. 新增 `/users` 转发规则（跟 `/api`、`/connector` 同风格），修复 SRP 登录第一步 `GET /users/auth/methods/:username` 在 vite dev 模式下到不了后端的问题——这是两个登录 E2E 用例超时的根因。
2. `/api` 规则加了 `rewrite: (path) => path.replace(/^\/api\/api\b/, '/api')`，只精确剥掉 `BASE = '/api/api'`（见 `<&frontend/src/api.ts>`）造成的双层前缀，模拟生产 nginx 网关"剥一层 /api"的效果；刻意用精确匹配而不是无条件剥一层——`terminal.py` 返回的终端 iframe 地址是单层 `/api/topics/.../terminal/live/`，必须原样不动才能命中后端真实路由，无条件剥层会把这条弄坏。

`VITE_API_BASE_URL` 不需要设置：`API_BASE_URL` 未设时 axios baseURL 是 `undefined`，请求走相对路径，纯靠 proxy 转发即可，跟 `/connector` 现状一致。

## 验证方式与结果

沙箱没有 docker，`playwright install --with-deps` 需要的系统级共享库（`libglib-2.0.so.0` 等）装不了，浏览器起不来（`chrome-headless-shell: error while loading shared libraries`）——这是沙箱本身的限制，不是本次改动引入的问题。已用其他方式做了等价验证：

- 起了真实 backend（`uvicorn`，接真实 Postgres——沙箱内用户态 `pgserver` + `fakeredis` TCP server 代替 docker 里的 PG/Redis）+ 真实 `vite` dev server，用 `curl` 走 dev proxy 实测：
  - `GET /users/auth/methods/alice` 经 3100 端口 proxy 拿到的响应跟直连后端 8791 完全一致（200，认证方式 JSON）——此前这个请求在 dev 模式下根本到不了后端。
  - `POST /users/auth/login`（alice/demo12345，seed 数据）经 proxy 返回 200 + `accessToken`，跟直连后端一致——对应第一个 E2E 用例的断言路径。
  - 错误凭据经 proxy 返回 401 + "Invalid username or password"文案——对应第二个 E2E 用例的断言路径。
  - `GET /api/api/projects`（双层前缀）经 proxy 现在正确落到 `/api/projects`，返回 200，不再 404。
  - 手写 echo-backend 桩验证过 `/api/topics/1/terminal/live/`（单层前缀，终端 iframe 场景）proxy 后原样不变，没被误剥层。
- `eslint` 对 `vite.config.ts` 干净。全量 `vue-tsc --noEmit` 在这个沙箱里跑到 OOM（跟本次改动无关，纯前端项目现有体量在受限内存下的已知限制），改动本身是纯配置对象字面量，vite 自己的 esbuild 加载器多次成功启动没报类型/语法错。

综合看，登录 E2E 用例的网络层根因已解），实际浏览器跑不了完全是沙箱环境限制，不是代码问题。

## 遗留 / 建议

浏览器级别的 Playwright 全量跑不了这件事本身值得单独反馈——如果以后还有话题需要在沙箱里跑真实浏览器 E2E，需要有预装好系统依赖的沙箱镜像（`playwright install --with-deps` 需要 apt/root），当前沙箱不具备。这次改动本身不受影响，只是没法在本沙箱里拿到 Playwright 自己跑出的绿色报告作为最终证据。
