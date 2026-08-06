# 权限纪律审计（融合文档 §4 的落点，喂给 P1）

对照 `design/cheese-agent-layer` 的权限纪律（architecture §3 / `viewer_authz.py`）审我们现状。

## 现状（实证）

- **人类 API 无真鉴权**：`require_auth_user` 在路由里用 **0 次**；`/api/users/login` 只按
  handle+name 建/返回 User 行，**不发 token**（Phase-0 极简登录：handle 即身份、无密码）。
- **actor 从 request body 信任读取**：`chat.py:78`、`topics.py:153/248` 等
  `body.get("author")`——正是他们纪律警告的反模式（"actor 永不从 body 读"）。
- **唯一真鉴权 = `cheese_token_gate`**（main.py）：只保护 sandbox 写端点（cheese CLI）的
  scoped token，是给 **agent** 的，不覆盖人类调用。
- User 域已存在（login 建 User 行）——agent-as-user 的地基现成。

## 差距（对照他们的纪律）

| 纪律 | 我们 | 差距 |
|---|---|---|
| actor 在信任边界注入，永不从 body 读 | body.get("author") | **大**：需真鉴权 + 注入 |
| 权限属于项目，可撤销 | 无项目级权限检查 | 中：需 project 权限模型 |
| 组合式授权策略（纯函数+注入适配器，可单测） | 分散/缺失 | 中 |
| token 必要非充分（每次按真实权限授权） | 无 token、无授权 | 大 |
| 群是共享访问单位 | 无（P0-2 成员名册补） | 由 P0-2 + P1 合力 |

## 补救（P1 实现范围）

1. **真人类鉴权**：login 发 token（JWT/session，参考他们 `common/auth.decode_token`
   `type==access`）；调用带 token；actor 在信任边界从 token 解出,**删掉所有 body.get("author")**
   （前端已有 me.ts 存 handle，改为存 token）。
2. **agent 鉴权对齐**：agent 走 scoped/session token（现有 cheese_token_gate 已是雏形），
   resolve 到 agent-user；人和 agent 都解析成 actor(user_id/handle)，业务永不 `if is_agent`。
3. **组合式授权**：把授权抽成纯策略函数 + 注入适配器（resolve_user / is_project_member /
   shares_topic），脱离 DB/WS 可单测（照抄他们 viewer_authz 形态）。
4. **项目级权限**：一个权限分享给项目→项目内所有人/agent 都有；每次调用按 actor 真实权限授权。
5. **平滑迁移**：Phase-0 handle 兼容期——token 里带 handle，服务层先接受 handle 也接受 token
   解出的 actor，逐步收紧到只认 token。

## 与 agent-as-user 的关系

P1 的两半是**同一件事**：要"actor 在信任边界注入"就需要真身份(user)；agent-as-user 让人和
agent 都是 user、同一套授权。所以 P1 = [真鉴权 + actor 注入] × [agent=user] 合并做。
