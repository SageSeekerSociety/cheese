## 现状核实

- 验收闸门（`.claude/scripts/check.sh` / `task check`）只跑 ruff + pyright + pytest，**不含 e2e** —— 反馈准确。
- `e2e.yml` 实际上在 `pull_request` 和 `push(main)` 上都会跑，并不是"合入后才跑一次"；但它**不是 PR 的必需检查**（无法在仓库文件里确认 branch protection 配置，需要有权限的人在 GitHub 仓库设置里确认），红了也不阻止合并——效果上等同于反馈说的"不影响任何决策"。
- 部署闸门确实没看 e2e：`deploy.yml`（etrip 备用目标）和 `deploy-prod.yml`（cheese.ruc.edu.cn 现役 prod）的 `wait-for-ci` 都只等 `build-backend` + `test` 两个 check，从未等过 `e2e`。`deploy-dev.yml`（dev 盒子持续部署）更进一步：只要镜像 build 成功就直接部署，完全不看任何测试结果。
- `e2e/tests/smoke.spec.ts` 原来 3 个用例里 2 个是空断言（标题非空 / body 可见），且健康检查用例硬编码了 `localhost:8081`——CI 上这个端口实际是 dev 盒子上另一个无关的、已经在跑的部署，不是这次构建出来的那个，等于测了个寂寞（顺手发现并修了）。

## 已落地的改动

1. **`e2e/tests/`**：新增 `auth.spec.ts`（真实登录表单 + 错误凭据两个用例）、`topic-and-chat.spec.ts`（新建话题、发消息两个用例，走真实 UI + WS），改写 `smoke.spec.ts`（去掉两个空断言，只留健康检查，并修了端口 bug）。新增 `helpers.ts` 封装登录/进项目的公共步骤。用的是种子账号 `alice/demo12345`（`backend/alembic/versions/219831eb75a3_seed_demo_data.py`）。
   - **未在本沙箱内实跑**：沙箱没有 pnpm/Playwright/Postgres，只能靠读源码核对选择器和路由。合入前需要在有完整环境（dev 盒子或 CI）跑一遍确认。
2. **`deploy.yml` / `deploy-prod.yml`**：`wait-for-ci` 增加等待 `e2e` check 的步骤，和已有的 `build-backend`/`test` 并列。发布不会再在 e2e 红灯的情况下上线。这一步是纯粹的"部署前多等一个已存在的检查"，不影响任何人的 PR 流程，判断为低风险，已直接改。

## 待拍板：e2e 要不要成为 PR 合并的强制闸门

这条按简报要求上报，不单方面改：

- **方案 A：e2e 设为 PR 必需检查（阻断合并）**。优点：改动没过 e2e 就进不了 main，最彻底。缺点：仓库现在只有单台 self-hosted runner（`test.yml`/`e2e.yml` 的 concurrency 注释都提到这点），e2e 要额外起 Postgres/Redis/装浏览器/跑两个 dev server，比现有 pytest 慢不少，会让所有触发 `backend/**`、`frontend/**`、`e2e/**` 路径的 PR 排队变慢；WS/浏览器类测试比单元测试更容易偶发抖动，抖动会挡住无关 PR。且需要有仓库权限的人去 GitHub branch protection 里加必需检查，不是改代码能做到的。
- **方案 B：PR 不强制，但部署强制**（本次已落地 `deploy.yml`/`deploy-prod.yml` 那部分）。优点：不拖慢日常 PR 节奏。缺点：main 分支本身可能带着红 e2e 停留一段时间才被发现，别人在其基础上开发不会有任何提示。
- **方案 C：折中——挑一小撮最快最稳的用例（比如登录）设成 PR 必需检查，完整套件仍只在部署前强制**。兼顾"PR 阶段有真信号"和"不为了全量 e2e 拖慢每个 PR"，但需要维护两层用例分类，多一份复杂度。

个人倾向 C，但这是影响所有人 PR 体验的流程决策，请主话题这边拍板。
