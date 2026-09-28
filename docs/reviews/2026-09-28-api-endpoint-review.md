# 平台 API 逐接口优化审查 · 2026-09-28

范围：后端 `app/api/routes/` 下全部 **692 个 HTTP 接口 + 3 个 WebSocket 通道**（`chat.py`、`app_preview.py`、`llm_tunnel.py`），一个不落地逐个看过。路由清单由 `app/api` 的装饰器静态解析生成，与 `main.py` 的自动挂载一致。

判断参照取 GitHub REST/GraphQL 的公开惯例（分页 `link` 头、错误体 `message`/`documentation_url`/`status`、`X-RateLimit-*`、ETag/304、`Idempotency-Key`），辅以 Stripe、skills.sh；条目与出处见文末「参照」。**响应信封 `{"code","message","data"}` 是项目规范，本次不换 shape**，只在它内部对齐。

结论三档：`可优化`（有具体问题与可行改法）／`暂无`（看过，没有值得动的）／`待确认`（现象在，但需要产品或运维拍板）。

## 总览

| 结论 | 接口数 |
| --- | --- |
| 可优化 | 381 |
| 暂无 | 305 |
| 待确认 | 6 |
| 待分析 | 0 |
| **合计** | **692** |

另有 3 个 WebSocket 通道（`/topics/{topic_id}/chat`、`/preview/tunnel`、`/tunnel`）和 1 个条件注册的上传路由（`uploads.py`，仅 local 存储时挂载）；`routes/activities.py` 是有意停用的模块（无路由挂载），不在清单内。

## 账号与用户（`users`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/users/lookup` | `lookup_account_route` | 可优化 | 精确匹配且走 `lower(username)`/`lower(email)` 唯一索引，无查询问题；只是 `avatar_id` 是 snake_case，同族其余字段全是 camelCase |
| 2 | DELETE | `/users/me/team-requests/{requestId}` | `cancel_my_join_request` | 暂无 | 204 + 服务层按 `user_id` 限定，越权面已封 |
| 3 | DELETE | `/users/me/teams/{teamId}` | `leave_team` | 暂无 | 退队复用 `TeamService`，无额外查询 |
| 4 | POST | `/users/me/team-invitations/{invitationId}/accept` | `accept_team_invitation` | 暂无 | 204，服务层带 `user_id` 归属校验 |
| 5 | POST | `/users/me/team-invitations/{invitationId}/decline` | `decline_team_invitation` | 暂无 | 同 4 |
| 6 | GET | `/users/me/team-requests` | `list_my_team_requests` | 可优化 | `pageStart`/`pageSize` 无 `ge/le`：负值会走到 SQL `OFFSET/LIMIT`，pageSize 无上限；status 解析 20 行与 7 重复 |
| 7 | GET | `/users/me/team-invitations` | `list_my_team_invitations` | 可优化 | 同 6（两段 handler 除类型外逐字相同） |
| 8 | GET | `/users/{userId}/follow/questions` | `get_user_followed_questions` | 可优化 | 与 40 的 handler 逐字重复；不校验目标用户存在（9 校验）；`pageStart` 未校验 |
| 9 | GET | `/users/{userId}/questions` | `get_user_questions` | 可优化 | `pageStart` 未校验（负值→500）；与 8/40 三份同样的 DTO 拼装循环 |
| 10 | GET | `/users/{userId}/answers` | `get_user_answers` | 可优化 | 先全量拉该用户所有答案 id 再 `list.index()` 算游标，用户答案多时线性内存与耗时；`pageStart` 语义与同族其余端点不同 |
| 11 | POST | `/users/verify/email` | `send_register_email_code` | 可优化 | 未鉴权、且 409 判定在任何节流之前，等于一个不限速的「这个邮箱注册了吗」判定器 |
| 12 | GET | `/users/registration-config` | `get_registration_config` | 暂无 | 公开只读，无 DB 访问 |
| 13 | POST | `/users` | `register_user` | 暂无 | 校验顺序（邀请码→用户名→密码→邮箱码）合理，`consume_code` 原子；仅响应体 `code` 与 HTTP 状态不一致（见 M3） |
| 14 | GET | `/users/me` | `get_current_user` | 可优化 | 每次 8 条查询（user+profile+6 个 count），是前端热路径；平台已有条件请求件可挂 ETag |
| 15 | POST | `/users/me/email/code` | `send_add_email_code` | 暂无 | 15 分钟登录窗口 + 已有邮箱拒绝 + 配额，检查齐 |
| 16 | POST | `/users/me/email` | `add_email` | 暂无 | 先查重再花码，`IntegrityError` 兜底，顺序正确 |
| 17 | GET | `/users/me/auth-methods` | `get_my_auth_methods` | 暂无 | 2 次查询（count + 2FA 事实），只给本人 |
| 18 | POST | `/users/me/sudo/email-code` | `request_sudo_email_code` | 暂无 | 发信前重查 2FA 与占位邮箱，配额同族共用 |
| 19 | GET | `/users/{userId}` | `get_user` | 可优化 | 公开资料页同样触发 6 个 count；可挂 ETag 或让计数可选 |
| 20 | PATCH | `/users/{userId}` | `patch_user_profile` | 可优化 | body 是裸 `dict`，`{"nickname":123}` 会在 `normalize_nickname` 的 `.strip()` 上 AttributeError→500 |
| 21 | PUT | `/users/{userId}` | `put_user_profile` | 可优化 | 与 PATCH 函数体逐字相同、参数相同，PUT 并没有「整体替换」语义 |
| 22 | POST | `/users/auth/login` | `user_login` | 可优化 | 每次成功登录都铸造并预约一张 sudo 票据（Redis 写）即使不需要；未装 `_require_same_origin` |
| 23 | POST | `/users/auth/verify-2fa` | `verify_2fa_login` | 可优化 | body 裸 `dict` + 手写取值，票据/预算逻辑本身正确 |
| 24 | POST | `/users/auth/email-code` | `request_sign_in_code` | 暂无 | 配额在查账号之前、邮件 spawn 到响应之后、未知地址同答复，反枚举做对了 |
| 25 | POST | `/users/auth/email-code/verify` | `verify_sign_in_code` | 暂无 | 共享 `email_code` 客户端预算，2FA 分支与密码路径一致 |
| 26 | POST | `/users/auth/refresh-token` | `refresh_access_token` | 可优化 | 每次刷新都重算整个 user DTO（8 条查询），而客户端手上已有该对象 |
| 27 | POST | `/users/auth/logout` | `user_logout` | 暂无 | 幂等，清除 cookie，装了同源校验 |
| 28 | POST | `/users/auth/sudo` | `sudo_auth` | 可优化 | 160 行 if/elif 单函数，四种方法各自开 Redis、各自校验，可拆 |
| 29 | GET | `/users/{userId}/identity` | `get_user_identity` | 可优化 | `sudoTicket` 走 query string：凭据进 URL、进访问日志、进 Referer |
| 30 | PUT | `/users/{userId}/identity` | `put_user_identity` | 暂无 | 票据在 body、先验票后写，正确 |
| 31 | PATCH | `/users/{userId}/identity` | `patch_user_identity` | 可优化 | 读-改-写两步，两并发 PATCH 会互相覆盖 |
| 32 | DELETE | `/users/{userId}/identity` | `delete_user_identity` | 暂无 | 独立 purpose 的票据，语义清楚 |
| 33 | GET | `/users/{userId}/identity/access-logs` | `get_user_identity_access_logs` | 可优化 | `pageStart` 未校验；`pageSize` 已有 `le=200` |
| 34 | GET | `/users/me/sessions` | `list_sessions` | 暂无 | 2 条查询，`current`/`trusted` 标记一次算清 |
| 35 | DELETE | `/users/me/sessions/{sessionId}` | `revoke_session` | 暂无 | 返回体而非 204 与同族 3/4 不一致，但带 `user_id` 过滤，安全面正确 |
| 36 | DELETE | `/users/me/sessions` | `revoke_all_sessions` | 暂无 | `keep=current` 语义正确 |
| 37 | POST | `/users/recover/password/request` | `recover_password_request` | 暂无 | 配额先花、邮件 spawn、未知地址同答复 |
| 38 | POST | `/users/recover/password/verify` | `recover_password_verify` | 暂无 | 一次性 token + 全量吊销会话/信任设备 |
| 39 | PATCH | `/users/{userId}/password` | `change_password` | 暂无 | 票据 + 保留当前会话的吊销策略一致 |
| 40 | GET | `/users/{userId}/favorites/questions` | `get_user_favorite_questions` | 可优化 | 与 8 逐字重复（问题只有「关注」一种收藏关系，语义没错，是代码重复） |
| 41 | GET | `/users/{userId}/favorites/answers` | `get_user_favorite_answers` | 可优化 | 循环内每条答案一次 `get_profile_by_user_id`，页大小 100 就是 100 次往返 |
| 42 | GET | `/users/{userId}/settings` | `get_user_settings` | 可优化 | 返回写死的四个默认值，不是账户真实设置 |
| 43 | PATCH | `/users/{userId}/settings` | `update_user_settings` | 可优化 | 不回写任何存储，把入参回显当成「保存成功」 |
| 44 | GET | `/users` | `list_users` | 可优化 | 最重：每行 2 次 `get_by_id` + 每次 `build_user_dto` 6 个 count（8N+1 条）；`q` 在 Python 里过滤；`hasMore` 用 `returned == page_size` 判，过滤后失真 |
| 45 | POST | `/users/{userId}/2fa/enable` | `enable_user_2fa` | 暂无 | 两段式：第一段花票，第二段只认第一段存下的 pending secret，不可跳过 |
| 46 | POST | `/users/{userId}/2fa/disable` | `disable_user_2fa` | 可优化 | `_notify_2fa_disabled` 在请求路径上 await SMTP（单次超时 30s，回退发送器最多两次），响应会被邮件拖住 |
| 47 | GET | `/users/{userId}/2fa/status` | `get_user_2fa_status` | 暂无 | 2 次查询，只给本人 |
| 48 | POST | `/users/{userId}/2fa/backup-codes` | `regenerate_backup_codes` | 暂无 | 票据 purpose 独立，重发即作废旧码 |
| 49 | POST | `/users/{userId}/passkeys/options` | `passkey_register_challenge` | 暂无 | 花票 + challenge 存 Redis 300s，单次注册绑定 |
| 50 | POST | `/users/{userId}/passkeys` | `passkey_register_verify` | 暂无 | challenge 存在性检查在验证之前，且一次性删除 |
| 51 | POST | `/users/{userId}/passkeys/prompt/dismiss` | `dismiss_passkey_prompt` | 暂无 | 只在 offer 到期时计数，双击不会推进轮次 |
| 52 | POST | `/users/auth/passkey/options` | `passkey_authenticate_challenge` | 暂无 | challenge 随机、300s TTL；存进 Redis 的 `userId` 目前没被校验方使用（死数据，非漏洞） |
| 53 | POST | `/users/auth/passkey/verify` | `passkey_authenticate_verify` | 暂无 | 断言是签名不是口令，无口令猜测面；登录方式计入 sudo 窗口 |
| 54 | GET | `/users/{userId}/passkeys` | `list_passkeys` | 暂无 | 单查询 + 只给本人 |
| 55 | DELETE | `/users/{userId}/passkeys/{credentialId}` | `delete_passkey` | 暂无 | 花票 + 删除按 `user_id` 限定 |
| 56 | GET | `/users/auth/oauth/providers` | `get_oauth_providers` | 暂无 | 只回 id/name/scope，未泄漏 secret |
| 57 | GET | `/users/auth/oauth/login/{providerId}` | `get_oauth_login_url` | 暂无 | state 服务端预约 + httpOnly cookie 绑定浏览器，回跳路径按 provider 的 redirect_url |
| 58 | GET | `/users/auth/oauth/callback/{providerId}` | `handle_oauth_callback` | 可优化 | `except Exception` 把任何异常转成 302 错误页（含编程错误）；`state` 明文在 URL |
| 59 | GET | `/users/auth/oauth/state` | `get_oauth_state` | 可优化 | 未鉴权 + bearer 票据在 query string + 解码不消费，票据 15 分钟内可被任何拿到 URL 的人重放读取；响应无 `Cache-Control: no-store` |
| 60 | POST | `/users/auth/oauth/email/code` | `send_oauth_email_code` | 暂无 | 只认已签发的 stateToken，受地址配额限制 |
| 61 | POST | `/users/auth/oauth/email/verify` | `verify_oauth_email` | 暂无 | 邮箱属于他人时转 ownership 页，不按邮箱静默绑定 |
| 62 | POST | `/users/auth/oauth/verify` | `oauth_verify_conflict` | 暂无 | pending session 取走即删（MULTI/EXEC），密码尝试走登录同一个 delay |
| 63 | POST | `/users/oauth/create` | `oauth_create_user` | 可优化 | 145 行单函数；票据在诸多检查之后才消费（与 64 相反）；`except Exception` 一律 CREATION_FAILED |
| 64 | POST | `/users/oauth/bind` | `oauth_bind_user` | 可优化 | 票据在密码校验之前就被消耗：密码打错一次，整个 OAuth 决策流程作废；且直接用了 `auth_service._user_repo` 私有属性 |
| 65 | GET | `/users/invite-codes` | `list_invite_codes` | 可优化 | 被更早注册的 `GET /users/{userId}` 遮蔽，实际不可达（`invite-codes` 落到 int 转换上回 400），代码注释里已承认 |
| 66 | POST | `/users/invite-codes` | `create_invite_code` | 可优化 | `maxUses` 无 `ge/le`，可传 0 或负数或极大值 |
| 67 | DELETE | `/users/invite-codes/{code_id}` | `deactivate_invite_code` | 可优化 | 不存在的 id 也回 200 成功，DELETE 的幂等/404 语义应说明 |
| 68 | GET | `/users/{userId}/oauth/connections` | `list_oauth_connections` | 暂无 | 只回元数据，注释明确不回 token，只给本人 |
| 69 | DELETE | `/users/{userId}/oauth/connections/{connectionId}` | `delete_oauth_connection` | 暂无 | 花票 + 「最后一条登录途径」行锁保护，做得比同族细 |

## 空间（`spaces`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/spaces/{spaceId}` | `get_space` | 可优化 | 同一行 Space 一次请求里被独立读了 3 次（router 依赖 / 可见性 / 主体），可合并为 1 次。 |
| 2 | GET | `/spaces` | `get_spaces` | 可优化 | 管理员按人 hydrate，每位管理员 2 条 SQL，随页大小线性增长；同文件的批量写法没在这里用。 |
| 3 | POST | `/spaces` | `create_space` | 可优化 | `exists_by_name` 与 INSERT 之间存在竞态，`Space.name` 没有唯一索引兜底，可落进同名板。 |
| 4 | PATCH | `/spaces/{spaceId}` | `patch_space` | 暂无 | 字段校验齐、`*_set` 语义清楚，仅受 #1 的重复读影响。 |
| 5 | DELETE | `/spaces/{spaceId}` | `delete_space` | 暂无 | 204、软删、服务层判管理员，未见问题。 |
| 6 | POST | `/spaces/join` | `join_space` | 暂无 | 兑换是原子的、重复兑换幂等，返回整份 space 是前端 store 覆盖所需。 |
| 7 | POST | `/spaces/{spaceId}/enroll` | `enroll_in_course` | 待确认 | 幂等重入做得好，但响应里 `project.root_topic_id` 是 snake_case，与同响应其它 camelCase 字段不一致；需确认是否有意（前端 `cx_types.ts` 依赖它）。 |
| 8 | GET | `/spaces/{spaceId}/course-link` | `get_course_link` | 可优化 | GET 会写库（没有可用码时现造一张码），不满足「GET 安全/幂等」。 |
| 9 | GET | `/spaces/{spaceId}/members` | `list_space_members` | 可优化 | 无分页；且任何成员都能读到每条记录的 `inviteCode.code`（默认 50 次、永不过期），而签发/查看邀请码本身是管理员权限。 |
| 10 | GET | `/spaces/{spaceId}/course/roster` | `get_course_roster` | 暂无 | 管理员门 + `_hydrate_people` 批量，未见问题。 |
| 11 | GET | `/spaces/{spaceId}/course/my-group` | `get_my_course_group` | 暂无 | 查自己那行，团队信息批量 hydrate，未见问题。 |
| 12 | POST | `/spaces/{spaceId}/members` | `add_space_member` | 暂无 | 先判可见性再判用户存在，随后批量 hydrate 一行，写法规范。 |
| 13 | DELETE | `/spaces/{spaceId}/members/{userId}` | `delete_space_member` | 暂无 | 204、可见性在前、服务层判管理员，未见问题。 |
| 14 | POST | `/spaces/{spaceId}/leave` | `leave_space` | 暂无 | 204、幂等，未见问题。 |
| 15 | GET | `/spaces/{spaceId}/invite-codes` | `list_space_invite_codes` | 暂无 | 管理员门在服务层（对不存在与无权限同为 403，不泄漏存在性），`createdBy` 批量。 |
| 16 | POST | `/spaces/{spaceId}/invite-codes` | `create_space_invite_code` | 可优化 | `expiresAt` 直接 `datetime.fromtimestamp(ms/1000)`，越界值触发 500 而不是 422/400。 |
| 17 | PATCH | `/spaces/{spaceId}/invite-codes/{codeId}` | `patch_space_invite_code` | 可优化 | 同上越界 500；其余字段的 set/clear 语义清楚。 |
| 18 | DELETE | `/spaces/{spaceId}/invite-codes/{codeId}` | `revoke_space_invite_code` | 暂无 | 204、撤销靠 `consume_use` 一处判据，未见问题。 |
| 19 | GET | `/spaces/{spaceId}/categories` | `list_space_categories` | 暂无 | 可见性门 + 单次列表查询，未见问题。 |
| 20 | GET | `/spaces/{spaceId}/analytics/tasks` | `get_space_task_analytics` | 可优化 | 无分页，返回空间内全部任务；`hasPending*` 在 Python 里后过滤。 |
| 21 | GET | `/spaces/{spaceId}/publishers/participation` | `get_publishers_participation` | 暂无 | 已标 `deprecated`，同 #25 的数据，单独看无新问题（成本随 #25 一并优化）。 |
| 22 | GET | `/spaces/{spaceId}/participants/export` | `export_space_participants` | 暂无 | 已标 `deprecated`；CSV 公式注入已有测试覆盖（`test_csv_export_formula_injection.py`）。 |
| 23 | GET | `/spaces/{spaceId}/analytics/overview` | `get_space_analytics_overview` | 可优化 | 每次请求 `_load_context` 全量载入本板 4 张表的行再在 Python 聚合。 |
| 24 | GET | `/spaces/{spaceId}/analytics/alerts` | `get_space_analytics_alerts` | 可优化 | 同上，为了几个提醒数字载入全板行。 |
| 25 | GET | `/spaces/{spaceId}/analytics/publishers` | `get_space_analytics_publishers` | 可优化 | 同上，发布者对比表也是全量载入后计算。 |
| 26 | GET | `/spaces/{spaceId}/analytics/participants` | `get_space_analytics_participants` | 可优化 | 同上，另加真实身份解密，全在内存里做。 |
| 27 | GET | `/spaces/{spaceId}/analytics/participants/export` | `export_space_analytics_participants` | 可优化 | 每个成员 3 条 SQL 写审计日志（2 次存在性检查 + 1 次 INSERT），人数即往返数。 |
| 28 | GET | `/spaces/{spaceId}/submissions` | `get_space_submissions` | 可优化 | 分页规范，但每行再发 2 条 SQL（entry + review）；`summary_for_space` 又把全板报名行全取出来只为 `len()`。 |
| 29 | GET | `/spaces/{spaceId}/analytics/learning/filters` | `get_space_learning_filters` | 可优化 | 鉴权做得对（`_ensure_space_visible` + 逐项目 `may_read_project`），代价是 `learning_service.py:142` 那个逐项目权限循环：N 个项目 N 次检查，可批量 |
| 30 | GET | `/spaces/{spaceId}/analytics/learning/questions` | `get_space_learning_questions` | 可优化 | 同上逐项目权限循环；`total` 报的是返回条数（`len(items)`）不是真总数，`questions()` 已用 `QUESTION_LIMIT=300` 截断，前端会当成全量 |
| 31 | GET | `/spaces/{spaceId}/analytics/learning/queues` | `get_space_learning_queues` | 可优化 | 同上逐项目权限循环；`queues()` 里 `for question in questions`（`:421`）逐条加工，配上 300 条上限就是 300 轮 |
| 32 | POST | `/spaces/{spaceId}/analytics/learning/outline` | `build_space_learning_outline` | 可优化 | 同上逐项目权限循环；`outline()` 里 `for block_id in block_ids`（`:512`）逐条取块，可一次 `IN` 取回 |
| 33 | GET | `/spaces/{spaceId}/analytics/tasks/export` | `export_space_analytics_tasks` | 可优化 | 复用 `get_tasks`，因此继承 #20 的全量载入；导出全量本身合理，但可以在 SQL 侧聚合。 |
| 34 | GET | `/spaces/{spaceId}/analytics/publishers/export` | `export_space_analytics_publishers` | 可优化 | 同上。 |
| 35 | GET | `/spaces/{spaceId}/me/publishing` | `get_space_me_publishing` | 暂无 | 固定 5 条左右 SQL、范围是自己发的题，批量写法正确，未见问题。 |
| 36 | GET | `/spaces/{spaceId}/me/publishing/tasks` | `get_space_me_published_tasks` | 可优化 | 带任一筛选时会**再**发一条不带筛选的全量查询，就为了算可见性徽章。 |
| 37 | GET | `/spaces/{spaceId}/me/participating` | `get_space_me_participating` | 暂无 | `_load_context` 是按用户载入、已分批，未见问题。 |
| 38 | GET | `/spaces/{spaceId}/me/participations` | `get_space_me_participations` | 暂无 | 同上，范围是自己，未分页但天然有界。 |
| 39 | GET | `/spaces/{spaceId}/topics` | `get_space_topics` | 可优化 | 声明 `le=100` 却静默截到 50，客户端要 100 只会拿到 50 且无从察觉。 |
| 40 | POST | `/spaces/{spaceId}/categories` | `create_space_category` | 暂无 | 服务层判管理员，201 + 单对象，未见问题。 |
| 41 | PATCH | `/spaces/{spaceId}/categories/{categoryId}` | `patch_space_category` | 暂无 | `archived`/`archivedAt` 双写法兼容有注释说明，未见问题。 |
| 42 | GET | `/spaces/{spaceId}/categories/{categoryId}` | `get_space_category` | 暂无 | 可见性门 + 单行读，未见问题。 |
| 43 | DELETE | `/spaces/{spaceId}/categories/{categoryId}` | `delete_space_category` | 暂无 | 未见问题。 |
| 44 | POST | `/spaces/{spaceId}/categories/{categoryId}/archive` | `archive_space_category` | 暂无 | 未见问题。 |
| 45 | DELETE | `/spaces/{spaceId}/categories/{categoryId}/archive` | `unarchive_space_category` | 暂无 | 未见问题。 |
| 46 | GET | `/spaces/{spaceId}/domain-groups` | `list_space_domain_groups` | 暂无 | 按设计对所有登录用户开放（`list_domain_groups` 注释），未见问题。 |
| 47 | POST | `/spaces/{spaceId}/domain-groups` | `create_space_domain_group` | 暂无 | 服务层判管理员，未见问题。 |
| 48 | PATCH | `/spaces/{spaceId}/domain-groups/{groupId}` | `patch_space_domain_group` | 暂无 | 未见问题。 |
| 49 | DELETE | `/spaces/{spaceId}/domain-groups/{groupId}` | `delete_space_domain_group` | 暂无 | 未见问题。 |
| 50 | GET | `/spaces/{spaceId}/managers` | `list_space_admins` | 暂无 | 可见性门，避免泄漏「谁在管一个你没被邀请的板」，未见问题。 |
| 51 | POST | `/spaces/{spaceId}/managers` | `add_space_admin` | 暂无 | 角色字符串在路由校验，返回整份 payload 供前端 store 覆盖，未见问题。 |
| 52 | DELETE | `/spaces/{spaceId}/managers/{userId}` | `delete_space_admin` | 暂无 | 未见问题。 |
| 53 | PATCH | `/spaces/{spaceId}/managers/{userId}` | `patch_space_manager` | 暂无 | 未见问题。 |
| 54 | GET | `/spaces/{spaceId}/units` | `list_space_units` | 暂无 | `canTeach` + `quiz_ids_by_unit` 批量取小测 id，未见问题。 |
| 55 | POST | `/spaces/{spaceId}/units` | `create_space_unit` | 暂无 | 管理员门，未见问题。 |
| 56 | PATCH | `/spaces/{spaceId}/units/{unitId}` | `patch_space_unit` | 暂无 | 未见问题。 |
| 57 | DELETE | `/spaces/{spaceId}/units/{unitId}` | `delete_space_unit` | 暂无 | 未见问题。 |
| 58 | GET | `/spaces/{spaceId}/units/{unitId}/quiz` | `get_unit_quiz` | 暂无 | 提交/复核队列的人名走 `_attach_people_to` 两查询批量，未见问题。 |
| 59 | GET | `/spaces/{spaceId}/quizzes/{quizId}` | `get_space_quiz` | 暂无 | 同 #58。 |
| 60 | POST | `/spaces/{spaceId}/units/{unitId}/quiz` | `create_unit_quiz` | 暂无 | 管理员门，未见问题。 |
| 61 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}` | `patch_space_quiz` | 暂无 | 未见问题。 |
| 62 | DELETE | `/spaces/{spaceId}/quizzes/{quizId}` | `delete_space_quiz` | 暂无 | 未见问题。 |
| 63 | POST | `/spaces/{spaceId}/quizzes/{quizId}/questions` | `add_quiz_question` | 暂无 | 未见问题。 |
| 64 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}/questions/{questionId}` | `patch_quiz_question` | 暂无 | 未见问题。 |
| 65 | DELETE | `/spaces/{spaceId}/quizzes/{quizId}/questions/{questionId}` | `delete_quiz_question` | 暂无 | 未见问题。 |
| 66 | PUT | `/spaces/{spaceId}/quizzes/{quizId}/my-attempt` | `submit_quiz_attempt` | 可优化 | 换卷是「清空 + 逐题 INSERT」，N 道题 N 条 SQL；动词与幂等语义这里是对的。 |
| 67 | PATCH | `/spaces/{spaceId}/quizzes/{quizId}/answers/{answerId}` | `grade_quiz_answer` | 暂无 | 管理员门 + 单行更新，未见问题。 |

## 话题（`topics`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | POST | `/topics` | `create_topic` | 可优化 | actor 解析两次、`TopicService` 构造两次；无可选幂等键，超时重发会多开一间房 |
| 2 | GET | `/topics` | `list_topics` | 可优化 | 全量返回、没有任何分页；`total` 由一次与结果同源的冗余 COUNT 得出 |
| 3 | GET | `/topics/{topic_id}` | `get_topic` | 暂无 | 单房间约 14 次定长查询，均为批量件且无 N+1；未见越权 |
| 4 | GET | `/topics/{topic_id}/blocks` | `list_topic_blocks` | 可优化 | 默认路径（无 limit）多跑一次与 `list_for_topic` 谓词完全相同的 COUNT |
| 5 | GET | `/topics/{topic_id}/history` | `read_chat_history` | 可优化 | 缺 `total`（与 `/blocks` 不一致）；`q` 走三段 ILIKE，无索引可依 |
| 6 | GET | `/topics/{topic_id}/history/{block_id}` | `read_chat_message` | 暂无 | 单块 + 反应各一次查，房间作用域检查到位 |
| 7 | GET | `/topics/{topic_id}/tasks` | `list_room_tasks` | 可优化 | 默认 `limit=None` 时把房间全部活（自述近 200 条）的对话整份序列化；无房间级上限 |
| 8 | GET | `/topics/{topic_id}/tasks/{task_id}` | `get_room_task` | 暂无 | 批量件用单元素列表调用，代价定长 |
| 9 | POST | `/topics/{topic_id}/tasks/{task_id}/messages` | `say_on_task` | 可优化 | `body: dict` 无 schema，无 OpenAPI、无字段级校验 |
| 10 | POST | `/topics/{topic_id}/tasks/{task_id}/title` | `set_task_title` | 可优化 | 同上（低） |
| 11 | POST | `/topics/{topic_id}/tasks/{task_id}/close` | `conclude_task` | 暂无 | 用 `ConclusionIn`，有校验；`close_thread` 幂等 |
| 12 | GET | `/topics/{topic_id}/transcript` | `topic_transcript` | 可优化 | `total` 返回的是**本页条数**（`/blocks` 是全会话数），同名字段两种含义 |
| 13 | GET | `/topics/{topic_id}/transcript/{block_id}/output` | `step_output` | 暂无 | 单块读，作用域（房间/非卡/kind=event）三重校验 |
| 14 | GET | `/topics/{topic_id}/usage` | `topic_usage` | 暂无 | 一次聚合查询 |
| 15 | GET | `/topics/{topic_id}/status` | `topic_status` | 可优化 | 验收卡无上限（含历史全部）；`shutil.disk_usage` 同步跑在事件循环上 |
| 16 | GET | `/topics/{topic_id}/children` | `list_topic_children` | 暂无 | 一次查子话题；`total` 语义正确 |
| 17 | GET | `/topics/{topic_id}/docs` | `list_topic_docs` | 可优化 | 文档节点无 limit（低） |
| 18 | GET | `/topics/{topic_id}/comments` | `list_comments` | 可优化 | 段落评论无 limit，全量返回（低） |
| 19 | POST | `/topics/{topic_id}/comments` | `add_comment` | 可优化 | `body: dict` 无 schema；`uuid.UUID(anchor)` 裸抛 ValueError |
| 20 | GET | `/topics/{topic_id}/progress` | `get_topic_progress` | 暂无 | 一次读 |
| 21 | PUT | `/topics/{topic_id}/progress` | `write_topic_progress` | 暂无 | 用 `ProgressIn`；整表替换天然幂等；commit 在 publish 之前 |
| 22 | GET | `/topics/{topic_id}/doc` | `get_topic_doc` | 暂无 | 一次读 |
| 23 | GET | `/topics/{topic_id}/overview` | `get_topic_overview` | 暂无 | 一次装配 |
| 24 | PUT | `/topics/{topic_id}/doc` | `edit_topic_doc` | 暂无 | 乐观并发（`expected_version` → 409）的正面样板 |
| 25 | GET | `/topics/{topic_id}/compute-profile` | `get_topic_compute_profile` | 暂无 | 定长若干次查；无 N+1 |
| 26 | POST | `/topics/{topic_id}/sessions/{session_id}/work-lease` | `acquire_session_work_lease` | 待确认 | 全文件唯一不走 `ActorResolver` 的路由；`db` 只用于委派，未见本路由写入 |
| 27 | PUT | `/topics/{topic_id}/compute-profile` | `set_topic_compute_profile` | 可优化 | handler 单函数约 185 行、8 处函数内 import（代码质量） |
| 28 | POST | `/topics/{topic_id}/messages` | `publish_chat_message` | 暂无 | 幂等已用 `request_id` → `publication_id`；`_persist_assistant_message` 自行 commit |
| 29 | POST | `/topics/{topic_id}/ask` | `ask_options` | 可优化 | `body: dict` 无 schema |
| 30 | POST | `/topics/{topic_id}/note` | `leave_a_note` | 可优化 | `body: dict` 无 schema |
| 31 | POST | `/topics/{topic_id}/deliveries` | `ask_for_a_delivery` | 可优化 | `body: dict` 无 schema；`deliver_at` 内部已 commit |
| 32 | POST | `/topics/{topic_id}/summon` | `summon_agent` | 暂无 | 只提交轮次，无写；两种情况如实回答 |
| 33 | POST | `/topics/blocks/{block_id}/answer` | `answer_options` | 可优化 | `meta` 读改写无行锁，双击/重发会记两次并唤醒房间两轮；无幂等键 |
| 34 | POST | `/topics/{topic_id}/webhook-token` | `mint_webhook_token` | 可优化 | 每次 POST 都轮换；超时重发会作废调用方刚拿到的那张；无只读路由可查状态 |
| 35 | POST | `/topics/{topic_id}/decision` | `record_decision` | 暂无 | `idem.claim` + 显式 commit，模板级写法 |
| 36 | POST | `/topics/{topic_id}/weekly` | `record_weekly` | 可优化 | 其余逻辑与 `decision` 一致，唯独缺显式 `db.commit()` |
| 37 | POST | `/topics/{topic_id}/title` | `set_title` | 可优化 | `body: dict` 无 schema（低） |
| 38 | POST | `/topics/{topic_id}/title/undo` | `undo_title` | 暂无 | 显式 commit；`event_id` 解析失败有 400 |
| 39 | POST | `/topics/{topic_id}/read` | `mark_topic_read` | 可优化 | 依赖依赖树 teardown 提交，响应先于落库（低） |
| 40 | POST | `/topics/{topic_id}/archive` | `archive_topic` | 可优化 | 同上；且持有 `FOR UPDATE` 到 teardown 才提交（低） |
| 41 | GET | `/topics/{topic_id}/cleanup` | `cleanup_status` | 暂无 | 一次读；`enforce=True` |
| 42 | POST | `/topics/{topic_id}/unarchive` | `unarchive_topic` | 可优化 | 同 #40（低） |
| 43 | POST | `/topics/{topic_id}/split` | `split_topic` | 暂无 | `idem.claim` 已覆盖重发 |
| 44 | POST | `/topics/{topic_id}/tasks/{task_id}/check-result` | `record_check_result` | 暂无 | 显式 commit；`CheckResultIn` 有校验 |
| 45 | POST | `/topics/{topic_id}/lock` | `take_room_lock` | 暂无 | 不等待语义明确；`LockIn` 有校验 |
| 46 | POST | `/topics/{topic_id}/unlock` | `release_room_lock` | 暂无 | 同上 |
| 47 | POST | `/topics/{topic_id}/clone-from` | `clone_topic_from` | 暂无 | 对来源与目标**两端**分别授权，正确 |
| 48 | POST | `/topics/{topic_id}/tell` | `tell_topic` | 暂无 | `RelayIn` 有校验；显式 commit |
| 49 | POST | `/topics/{topic_id}/shown` | `show_in_room` | 可优化 | WS 帧在 commit **之前**广播（该 bug 在 `create_topic` 已修过一处） |
| 50 | GET | `/topics/{topic_id}/shown` | `list_shown` | 可优化 | 无 limit；把房间**全部历史** artifact 载入 Python 再去重 |
| 51 | POST | `/topics/{topic_id}/shown/save` | `save_shown_to_library` | 暂无 | 要求已验证的人（凭据过不了则 403），符合注释里说的「只有人能按」 |
| 52 | POST | `/topics/{topic_id}/documents/recalc` | `recalc_spreadsheet` | 可优化 | `_document_bytes` 同步读盘 / base64 在事件循环上（低） |
| 53 | POST | `/topics/{topic_id}/documents/convert` | `convert_document` | 可优化 | 同上（低） |
| 54 | GET | `/topics/{topic_id}/documents/revisions` | `list_document_revisions` | 可优化 | `revisions_in` 同步解 docx（zip+XML）阻塞事件循环 |
| 55 | POST | `/topics/{topic_id}/documents/revisions` | `decide_document_revisions` | 可优化 | `decide` 同样是同步解析；`version` 冲突返回 409 且带 `data.version`，好 |
| 56 | GET | `/topics/{topic_id}/preview` | `get_preview` | 暂无 | 已用 `asyncio.to_thread` 卸载阻塞调用，是本文件的正面样板 |
| 57 | GET | `/topics/{topic_id}/preview/file` | `preview_file` | 可优化 | `library.read_*` 同步读盘在事件循环上（低） |
| 58 | POST | `/topics/{topic_id}/attachments` | `upload_attachment` | 可优化 | 同步写资料库；`resolve` 走两遍（低） |
| 59 | GET | `/topics/{topic_id}/attachments/raw` | `attachment_raw` | 可优化 | docstring 声称「扩展名白名单，绝不吐出可执行 HTML」，但 `download=1` 整条绕过白名单（低，见正文） |
| 60 | GET | `/topics/{topic_id}/attachments/pdf` | `attachment_as_pdf` | 暂无 | 渲染是 async；响应头（nosniff / CSP / Cache-Control）齐备 |
| 61 | GET | `/projects/{project_id}/topic-unread` | `project_topic_unread` | 暂无 | 一次聚合；收件人来自凭据而非查询串 |
| 62 | GET | `/projects/{project_id}/private-unread` | `project_private_unread` | 暂无 | 同上 |
| 63 | POST | `/blocks/{block_id}/upgrade` | `upgrade_block` | 暂无 | 房间由 block 自己带，不由请求体说；`created=False` 时不重复落事件 |

## 项目与站点（`projects`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/projects/{project_id}/context/search` | `search_project_context` | 可优化 | 逐房间做一次 `can_access_topic`，每个房间约 8 条查询；同一件事在别处是 3 条。 |
| 2 | GET | `/projects/{project_id}/export` | `export_project` | 可优化 | 同样的逐房间鉴权 N+1，外加逐产物 `artifacts.versions()` 各一次查询。 |
| 3 | GET | `/projects/{project_id}/site` | `get_project_site` | 可优化 | 候选人 `index.html` 逐个串行取 blob，每个 blob 重解绑凭据并发起新 HTTP 客户端。 |
| 4 | POST | `/projects/{project_id}/site` | `publish_project_site` | 可优化 | 正确地在响应前提交（值得保持），但 `_snapshot` 仍逐个串行取 blob。 |
| 5 | GET | `/projects/resource-limits` | `resource_limits` | 暂无 | 只答部署级默认值（未传 `team_id`，不走团队覆写），不需要调用方身份。 |
| 6 | POST | `/projects` | `create_project` | 可优化 | `team_id` 有成员校验，`owner_handle` 没有：可以点名把项目种进别人的个人团队。 |
| 7 | GET | `/projects` | `list_projects` | 暂无 | 三方载荷（shell/team handle）已批量化；无调用方时明说 401 而不是空列表。 |
| 8 | GET | `/projects/by-task/{task_id}` | `projects_for_task` | 暂无 | 门在 `authorize_task`，载荷复用批量化 helper。 |
| 9 | GET | `/projects/by-team/{team_id}` | `project_for_team` | 暂无 | 与 `?team_id=` 用同一道 `authorize_team`，无 N+1。 |
| 10 | GET | `/projects/{project_id}` | `get_project` | 暂无 | 单行读取，多一次 `MemberService.manages` 是有意的权限字段。 |
| 11 | GET | `/projects/{project_id}/agents` | `list_project_agents` | 暂无 | 一个项目的 agent 数量有天然上界，逐行 `resolved()` 是内存操作。 |
| 12 | POST | `/projects/{project_id}/agents` | `create_project_agent` | 可优化 | 只 flush 不 commit：响应发出时事务还开着（前端保存后立刻重列，见详细分析）。 |
| 13 | PUT | `/projects/{project_id}/agents/{agent_id}` | `update_project_agent` | 可优化 | 同上，只 flush。 |
| 14 | DELETE | `/projects/{project_id}/agents/{agent_id}` | `deactivate_project_agent` | 可优化 | 同上（连 flush 都交给 service 内部），答 `deleted: true` 时尚未提交。 |
| 15 | PUT | `/projects/{project_id}/default-agent` | `set_project_default_agent` | 可优化 | 同上。 |
| 16 | GET | `/projects/{project_id}/library/raw` | `library_file_raw` | 可优化 | 在 `async` 路由里同步读文件（`read_library_file`），阻塞事件循环；响应头也没有条件请求。 |
| 17 | GET | `/projects/{project_id}/library` | `list_library` | 可优化 | `root.rglob("*")` + 逐项 `stat()` 在事件循环里同步跑，且结果不分页。 |
| 18 | GET | `/projects/{project_id}/artifacts` | `list_artifacts` | 可优化 | 全量返回，无分页参数。 |
| 19 | GET | `/projects/{project_id}/artifacts/{artifact_id}` | `read_artifact` | 可优化 | 同一件事问三遍库：`get_or_404` → `summary`（再 select 一次）→ `versions`。 |
| 20 | GET | `/projects/{project_id}/artifacts/{artifact_id}/compare` | `compare_artifact_versions` | 可优化 | 文本比较逐个串行取 blob；快照读盘同步。 |
| 21 | GET | `/projects/{project_id}/artifacts/{artifact_id}/versions/{card_id}/file` | `download_artifact_version` | 可优化 | 为了取一版而把全部版本列一遍；快照同步读盘。 |
| 22 | PATCH | `/projects/{project_id}/artifacts/{artifact_id}` | `rename_artifact` | 可优化 | 请求体是无类型 `dict`，无 schema、无 OpenAPI 文档。 |
| 23 | POST | `/projects/{project_id}/artifacts/{artifact_id}/merge` | `merge_artifact` | 可优化 | 同上；`into` 的存在性靠事后 `get_or_404`。 |
| 24 | DELETE | `/projects/{project_id}/artifacts/{artifact_id}` | `delete_artifact` | 暂无 | 权限路径 `_artifact_keeper` 干净，显式 commit。 |
| 25 | DELETE | `/projects/{project_id}/library` | `delete_library_file` | 暂无 | 不同步返回字节，纯文件系统操作，无 DB 写。 |
| 26 | GET | `/projects/{project_id}/decisions` | `list_decisions` | 可优化 | `list_by_kind_for_project` 无 limit，项目越大返回越大。 |
| 27 | GET | `/projects/{project_id}/weeklies` | `list_weeklies` | 可优化 | 同上。 |
| 28 | GET | `/projects/{project_id}/tasks` | `list_project_tasks` | 可优化 | 四批查询都做了，但没有分页；一个项目已含约 170 个房间时全量返回。 |
| 29 | POST | `/projects/{project_id}/memory` | `add_memory` | 可优化 | 已永久关闭，但答 422（参数错）而不是 410 Gone（这个端点没有了）。 |
| 30 | GET | `/projects/{project_id}/private-chat` | `get_private_chat` | 可优化 | 用 `GET` 创建资源；且沙箱签名令牌调用时，参与人检查整段被跳过。 |
| 31 | GET | `/projects/{project_id}/forge` | `get_project_forge` | 暂无 | 单行 + 单绑定查询。 |
| 32 | GET | `/projects/{project_id}/forge-attribution` | `get_forge_attribution` | 暂无 | 纯读 `project.settings`。 |
| 33 | PUT | `/projects/{project_id}/forge-attribution` | `save_forge_attribution` | 可优化 | flush 后回身调用本组 GET 路由：同一道鉴权做两遍。 |
| 34 | GET | `/projects/{project_id}/default-model` | `get_default_model` | 暂无 | 目录组装在内存里，`can_manage` 是唯一一次额外查询。 |
| 35 | PUT | `/projects/{project_id}/default-model` | `save_default_model` | 可优化 | 只 flush 不 commit。 |
| 36 | GET | `/projects/{project_id}/compute-configs` | `get_compute_configs` | 暂无 | 一批查询 + 内存聚合，无逐行往返。 |
| 37 | GET | `/projects/{project_id}/devices/{device_id}/sessions` | `list_device_sessions` | 可优化 | 两层 N+1 叠加：逐房间 `authorize_topic`，逐会话 `_agent_name`。 |
| 38 | PUT | `/projects/{project_id}/compute-configs` | `save_compute_configs` | 可优化 | 只 flush 不 commit。 |
| 39 | GET | `/projects/{project_id}/tier-policy` | `get_tier_policy` | 暂无 | 纯读 settings。 |
| 40 | PUT | `/projects/{project_id}/tier-policy` | `set_tier_policy` | 可优化 | 无类型 body + flush 后回身调用 GET 路由。 |
| 41 | GET | `/projects/{project_id}/topic-naming` | `get_topic_naming` | 暂无 | 纯读 settings。 |
| 42 | PUT | `/projects/{project_id}/topic-naming` | `set_topic_naming` | 可优化 | 无类型 body + flush 后回身调用 GET 路由。 |
| 43 | PUT | `/projects/{project_id}/owner` | `set_project_owner` | 可优化 | 无类型 body；只 flush 不 commit，而这条路由的后果最重。 |
| 44 | GET | `/projects/{project_id}/branch-protection` | `get_branch_protection` | 可优化 | 每次打开设置页最多 3 次串行 GitHub 调用（各 10s 超时），无缓存。 |
| 45 | PUT | `/projects/{project_id}/branch-protection` | `set_branch_protection` | 可优化 | 无类型 body；只 flush 不 commit。 |
| 46 | GET | `/projects/{project_id}/upstream` | `get_project_upstream` | 暂无 | 纯读 settings。 |
| 47 | PUT | `/projects/{project_id}/upstream` | `set_project_upstream` | 可优化 | 无类型 body；只 flush 不 commit。 |

## 任务、里程碑、例程与 git（`tasks`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | PUT | `/projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_sha}` | `save_task_snapshot` | 可优化 | 每个请求块一次 `asyncio.to_thread`；整包先落盘再校验摘要，坏包要重传一遍 |
| 2 | GET | `/projects/{project_id}/git/tasks/{task_id}/snapshots/latest` | `latest_task_snapshot` | 暂无 | 单查询 + `limit(1)`；没有备份时抛 `NotFoundError` 而不是回空 |
| 3 | GET | `/projects/{project_id}/git/tasks/{task_id}/snapshots/{snapshot_id}` | `download_task_snapshot` | 可优化 | 整份 bundle 读成 `bytes` 再交给 `Response`，最长 512 MiB 全驻内存 |
| 4 | POST | `/projects/{project_id}/git/tasks/{task_id}` | `open_task_workspace` | 可优化 | 把 GET handler 当函数调，token 与 task 都又验/又取了一遍；提交在响应前（正确） |
| 5 | GET | `/projects/{project_id}/git/tasks/{task_id}` | `task_workspace` | 可优化 | GET 里向外发 1–2 次远程请求（30s 超时）且 `binding` 查两遍；语义上不是安全方法 |
| 6 | POST | `/projects/{project_id}/milestones` | `create_milestone` | 可优化 | 人这条路没有显式 `commit`（见 M2）；无可选幂等键，重发多钉一个里程碑 |
| 7 | GET | `/projects/{project_id}/milestones` | `list_milestones` | 可优化 | 读接口里跑一条 UPDATE（`mark_overdue`）+ 另一条 COUNT；没有任何 limit |
| 8 | GET | `/projects/{project_id}/calendar` | `project_calendar` | 可优化 | 同上；`total` 返回值就是本页条数，语义与 `/milestones` 不同 |
| 9 | PUT | `/milestones/{milestone_id}` | `update_milestone` | 可优化 | 无显式提交（见 M2） |
| 10 | DELETE | `/milestones/{milestone_id}` | `delete_milestone` | 可优化 | 回 200 + 信封，而 `/tasks` 的同名动作回 204（见 M4） |
| 11 | GET | `/projects/{project_id}/routines` | `list_routines` | 可优化 | 无 limit；`total` 是本页条数 |
| 12 | POST | `/topics/{topic_id}/routines` | `create_routine` | 可优化 | 无幂等键，重发建两条 routine；`TopicService` 同一请求里造了两次 |
| 13 | GET | `/routines/{routine_id}` | `get_routine` | 暂无 | 2 条定长查询，`get` 未命中会抛，作用域由 routine 自己带 |
| 14 | PATCH | `/routines/{routine_id}` | `update_routine` | 暂无 | `exclude_unset` 传字段，显式 commit |
| 15 | POST | `/routines/{routine_id}/confirm` | `confirm_routine` | 暂无 | 要求是人（`_person`），显式 commit |
| 16 | POST | `/routines/{routine_id}/pause` | `pause_routine` | 暂无 | 不要求是人是有意的：停的是活，不是开活 —— 与 resume 的不对称正确 |
| 17 | POST | `/routines/{routine_id}/resume` | `resume_routine` | 暂无 | 要求是人 + 显式 commit |
| 18 | POST | `/routines/{routine_id}/run-now` | `run_routine_now` | 可优化 | 在请求里跑平台级 `dispatch_pending(limit=100)`，响应要等别人的投递发完 |
| 19 | DELETE | `/routines/{routine_id}` | `delete_routine` | 暂无 | 要求是人 + 显式 commit |
| 20 | GET | `/routines/{routine_id}/runs` | `list_runs` | 可优化 | `runs()` 内部有 50 条上限，但 `total` 恒等于本页条数，客户端看不出还有 |
| 21 | POST | `/routine-runs/{run_id}/report` | `report_run` | 暂无 | 显式 commit；只有执行的那个队友能报（有测试） |
| 22 | POST | `/tasks` | `create_task` | 可优化 | 无幂等键，超时重发建两道题；其余（显式提交、发题门）是对的 |
| 23 | GET | `/tasks/{taskId}/attachments` | `list_task_attachments` | 暂无 | 三道闸齐全；清单与「能不能下载」一起算，无 N+1 |
| 24 | POST | `/tasks/{taskId}/attachments` | `upload_task_attachment` | 可优化 | 无显式提交（见 M2）；大小限制只在上游 nginx |
| 25 | GET | `/tasks/{taskId}/attachments/{attachmentId}/download` | `download_task_attachment` | 可优化 | 整个文件读进内存；`download_count` 的自增靠 teardown 提交 |
| 26 | DELETE | `/tasks/{taskId}/attachments/{attachmentId}` | `remove_task_attachment` | 可优化 | 无显式提交（见 M2） |
| 27 | POST | `/tasks/publish/from-pdf/preview` | `preview_task_from_pdf` | 可优化 | 在请求里同步跑模型；无幂等键，重试再花一次 token |
| 28 | POST | `/tasks/publish/from-pdf/confirm` | `confirm_publish_task_from_pdf` | 可优化 | 每道草稿都重查同一个 space / 分类 / `may_publish_in_space` |
| 29 | POST | `/tasks/{taskId}/participants` | `create_task_participant` | 可优化 | 「先查后插」无唯一约束兜底（见 M3）；无显式提交 |
| 30 | POST | `/tasks/{taskId}/participations/user` | `join_task_as_user` | 可优化 | 同上 |
| 31 | POST | `/tasks/{taskId}/participations/team` | `join_task_as_team` | 可优化 | 同上 |
| 32 | PATCH | `/tasks/{taskId}/participants/{participantId}` | `patch_task_participant` | 可优化 | 无显式提交（见 M2） |
| 33 | GET | `/tasks/{taskId}` | `get_task` | 可优化 | 同一个请求里三组查询各查两遍；`_enrich_task_models` 顺带拉整个板的报名表 |
| 34 | PATCH | `/tasks/{taskId}` | `patch_task` | 可优化 | 单函数约 240 行、字段逐个 if；topics 覆盖写不做任何校验（其余正确） |
| 35 | GET | `/tasks` | `get_tasks` | 可优化 | 全板报名表全量载入 + `accessDomainGroupIds` N+1 + `distinctParticipants` 再跑同一条查询 |
| 36 | DELETE | `/tasks/{taskId}` | `delete_task` | 可优化 | Python 循环逐行软删成员；无显式提交（见 M2） |
| 37 | DELETE | `/tasks/{taskId}/participants/{participantId}` | `delete_task_participant` | 可优化 | 无显式提交（见 M2） |
| 38 | DELETE | `/tasks/{taskId}/participants` | `delete_task_participant_by_member` | 可优化 | 同上 |
| 39 | PATCH | `/tasks/{taskId}/participants` | `patch_task_membership_by_member` | 可优化 | 返回全部参与者、每人只有 `{id}`，与 GET 名单的形状不一致；无分页、无提交 |
| 40 | POST | `/tasks/{taskId}/resubmit` | `resubmit_task` | 可优化 | 无显式提交：客户被告知「已重提」后立刻读，可能还是旧状态（见 M2） |
| 41 | GET | `/tasks/{taskId}/participants` | `get_task_participants` | 可优化 | 无分页、整份报名表返回（含 email/phone） |
| 42 | GET | `/tasks/{taskId}/participants/{participantId}` | `get_task_participant` | 暂无 | 判据与列表版一字不差，403/404 口径在 docstring 里写明 |
| 43 | GET | `/tasks/{taskId}/teams` | `get_task_teams` | 可优化 | `filter=eligible` 与 `all` 行为相同 —— 参数是个空承诺（源码自认） |
| 44 | GET | `/tasks/{taskId}/participants/{participantId}/submissions` | `get_task_submissions` | 可优化 | 每份提交 1–2 条查询（N+1）；membership 用全量名单挑一个 |
| 45 | POST | `/tasks/{taskId}/participants/{participantId}/submissions` | `post_task_submission` | 可优化 | 版本号「先查最新再 +1」无唯一约束兜底（见 M3） |
| 46 | PATCH | `/tasks/{taskId}/participants/{participantId}/submissions/{version}` | `patch_task_submission` | 暂无 | 校验链齐全，`version` 交给 service 判在不在 |
| 47 | POST | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | `post_task_submission_review` | 可优化 | 「先查有没有评审再插」+ 无唯一约束（见 M3）；无显式提交 |
| 48 | GET | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | `get_task_submission_review` | 暂无 | 鉴权照抄提交列表版（出题人/管理员/本人/小队成员） |
| 49 | PATCH | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | `patch_task_submission_review` | 可优化 | 无显式提交（见 M2） |
| 50 | PUT | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | `put_task_submission_review` | 可优化 | 名为 "Full Replace"，实际调的是 `patch_review`，缺字段不覆盖 —— 与 PATCH 无差别 |
| 51 | DELETE | `/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review` | `delete_task_submission_review` | 可优化 | 同一请求里 `get_review_dto` 查两遍；回 200 且带删除后的空 review |
| 52 | POST | `/tasks/{taskId}/ai-advice` | `request_task_ai_advice` | 可优化 | 在请求里同步跑一次完整 LLM 生成；无幂等键 |
| 53 | GET | `/tasks/{taskId}/ai-advice` | `list_task_ai_advice` | 可优化 | `list_by_task` 无 limit，每条含 `rawResponse` 之外的 4 段 JSON |
| 54 | GET | `/tasks/{taskId}/ai-advice/status` | `get_task_ai_advice_status` | 暂无 | 一次查询，只回状态 |
| 55 | GET | `/tasks/{taskId}/ai-advice/conversations/grouped` | `list_ai_advice_conversations_grouped` | 可优化 | 无分页：对话攒起来就是一整份返回 |
| 56 | GET | `/tasks/{taskId}/ai-advice/conversations/{conversationId}` | `get_ai_advice_conversation` | 可优化 | 就是这个先注册的路由吃掉了下面的 `/stream`（见 #59）；messages 无 limit |
| 57 | POST | `/tasks/{taskId}/ai-advice/conversations` | `create_ai_advice_conversation` | 可优化 | 在请求里同步等一次 LLM completion（非流式）；无幂等键 |
| 58 | DELETE | `/tasks/{taskId}/ai-advice/conversations/{conversationId}` | `delete_ai_advice_conversation` | 可优化 | 无显式提交（见 M2） |
| 59 | GET | `/tasks/{taskId}/ai-advice/conversations/stream` | `stream_ai_advice_conversation` | 可优化 | **当前永不可达**（被 #56 遮蔽）；且前端按 `[PARTIAL]/[RESPONSE]/[DONE]` 解析，与本端发的 JSON 帧不是一套 |

## 问答与标签（`qa`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/questions/{question_id}/answers` | `list_answers` | 可优化 | 【高】请求侧要 `page_start`、前端发 `pageStart` → 翻页永远回到第一页；且每页先用「全量 id 列表」再每答 6 条统计查询（20 条约 124 次往返） |
| 2 | POST | `/questions/{question_id}/answers` | `create_answer` | 可优化 | 【低】「已答过」先查后插有竞态且答 400（GitHub 对重复资源用 409）；无 DB 唯一约束兜底 |
| 3 | POST | `/questions/{question_id}/answers/{answer_id}/vote` | `vote_answer` | 可优化 | 【低】空 body 默认 `"UPVOTE"` 不是合法值 → 400；`ensure_answer_in_question` 与 `vote_answer` 各查一次同一行 |
| 4 | DELETE | `/questions/{question_id}/answers/{answer_id}/vote` | `remove_answer_vote` | 可优化 | 【低】同上重复取数；未投票也答 200（幂等没问题，但和 3 的语义不对称） |
| 5 | GET | `/questions/{question_id}/answers/{answer_id}/vote` | `get_answer_votes` | 可优化 | 【低】同一行查两遍；返回值与 `GET /answers/{id}` 里的 `attitudes` 重复 |
| 6 | GET | `/questions/{question_id}/answers/{answer_id}/comments` | `list_answer_comments` | 可优化 | 【高】`include_subs=True` + 每条评论单独查 reaction/子回复/子计数，一页 20 条约 300 次往返 |
| 7 | POST | `/questions/{question_id}/answers/{answer_id}/comments` | `create_answer_comment` | 可优化 | 【中】`mentionedUserIds` 元素非 int（或传字符串）在 `services.py:56` 抛 `TypeError` → 500，不是 400 |
| 8 | DELETE | `/questions/{question_id}/answers/{answer_id}/comments/{comment_id}` | `delete_answer_comment` | 可优化 | 【低】为判作者拉整份 DTO（含子回复、reaction）；`discussion["sender"]` 为 None 时 `["id"]` → 500 |
| 9 | GET | `/questions/{question_id}/answers/{answer_id}` | `get_answer` | 可优化 | 【中】读接口里同步写 `answer_query_log`（不可缓存/无 ETag），加上 DTO 富化共约 8 次往返 |
| 10 | PUT | `/questions/{question_id}/answers/{answer_id}` | `update_answer` | 可优化 | 【中】用 `service.get_answer`（≈8 次往返）做父级校验，`ensure_answer_in_question`（1 次）就够，之后 `update_answer` 又取一遍 |
| 11 | DELETE | `/questions/{question_id}/answers/{answer_id}` | `delete_answer` | 可优化 | 【中】同上；函数体内临时 import `NotFoundError`（`answers.py:328`，另一处 `answers.py:270`） |
| 12 | PUT | `/questions/{question_id}/answers/{answer_id}/favorite` | `favorite_answer` | 可优化 | 【低】一次收藏发 4 条 SQL（ensure + ensure + 查已收藏 + 计数），最小 1–2 条可完成 |
| 13 | DELETE | `/questions/{question_id}/answers/{answer_id}/favorite` | `unfavorite_answer` | 可优化 | 【低】同上 |
| 14 | POST | `/questions/{question_id}/answers/{answer_id}/attitudes` | `attitude_answer` | 可优化 | 【低】与 #3/#4 是同一动作的两套入口；`user_attitude` 直接回显请求值（非法值也照回） |
| 15 | POST | `/comments/{commentId}/attitudes` | `update_attitude_to_comment` | 可优化 | 【低】重复 ensure 两次；成功文案是整句「You have expressed…」，其他端点一律 `"OK"` |
| 16 | GET | `/comments/{commentId}` | `get_comment_by_id` | 可优化 | 【中】路由里又取一遍 user + profile 并 `build_user_dto`（含 4 项计数），覆盖 service 已给的作者 DTO；形状与 `GET /comments/{type}/{id}` 不一致 |
| 17 | PATCH | `/comments/{commentId}` | `update_comment` | 暂无 | 校验与归属都在 service，形状与列表一致，未发现可优化点 |
| 18 | DELETE | `/comments/{commentId}` | `delete_comment` | 暂无 | 归属校验正确；仅响应体形状（`data: null`）与全仓不一致，已并入第三节 |
| 19 | GET | `/comments/{commentableType}/{commentableId}` | `get_comments` | 可优化 | 【中】任意 `commentableType`（如 `FOO`）不校验、静默返回空页；无 `total`；子回复 N+1 同 #6 |
| 20 | POST | `/comments/{commentableType}/{commentableId}` | `create_comment` | 可优化 | 【高】只校验 `QUESTION` 存在，`FOO`/`MATERIAL_BUNDLE` 一路写进 PG enum `CommentCommentabletypeEnum`（只有 3 个值）→ 500；路由里直接 `QuestionRepository(session=db)` 破坏分层 |
| 21 | GET | `/questions/trending` | `get_trending_questions` | 可优化 | 【高】恒为空数组：唯一写 `question_query_log` 的 `log_query` 全仓只有单测调用 |
| 22 | GET | `/questions/stats` | `get_question_stats` | 可优化 | 【中】`totalViews` 恒为 0（同上）；3 条 count 可并成 1 条 |
| 23 | GET | `/questions/search-terms` | `get_popular_search_terms` | 可优化 | 【高】恒为空数组：`log_search` 无生产调用方 |
| 24 | GET | `/questions` | `search_questions` | 可优化 | 【高】`pageStart` 不匹配同 #1；Meilisearch 分支每条命中再 `get_by_id`（最多 100 次串行）；不落搜索日志 |
| 25 | POST | `/questions` | `add_question` | 可优化 | 【中】`bounty` 无上限（0–20 规则只在 PUT bounty 里）、`type` 无取值域；201 只回 `{"id"}` 而 PUT 回整份 DTO |
| 26 | GET | `/questions/followed` | `list_followed_questions` | 可优化 | 【低】`pageStart` 参数名同 #1（这条 handler 里连 Python 形参都写成了 `pageStart`，`questions.py:181`） |
| 27 | GET | `/questions/{question_id}` | `get_question` | 可优化 | 【高】单次请求约 13 条串行查询；`view_count` 恒为 0；无 ETag/条件请求 |
| 28 | POST | `/questions/{question_id}/followers` | `follow_question` | 可优化 | 【中】已关注答 400（应为 409 或幂等 200）；`service._repo` 私有属性穿透；无唯一约束，并发可落重复行 |
| 29 | DELETE | `/questions/{question_id}/followers` | `unfollow_question` | 可优化 | 【低】未关注答 400；GitHub 的 DELETE 语义是 204 + 幂等 |
| 30 | PUT | `/questions/{question_id}/acceptance` | `accept_answer` | 可优化 | 【中】采纳对象放在 query（`?answer_id=`）而删除走路径；答案不属于该题时答 400（应为 404，同族其他地方都答 404） |
| 31 | DELETE | `/questions/{question_id}/accept` | `unaccept_answer` | 可优化 | 【低】同一资源两个名词（`acceptance` / `accept`）；权限路径与 #30 不一致（多一次角色解析，结果相同） |
| 32 | POST | `/questions/{question_id}/vote` | `vote_question` | 可优化 | 【低】空 body 默认 `"UPVOTE"` 非法 → 400；与 `/attitudes` 重复；全仓无调用方 |
| 33 | DELETE | `/questions/{question_id}/vote` | `remove_question_vote` | 可优化 | 【低】同上（`/attitudes` + `UNDEFINED` 已覆盖） |
| 34 | GET | `/questions/{question_id}/vote` | `get_question_votes` | 暂无 | 只读、单条查询、有鉴权，未发现可优化点 |
| 35 | GET | `/questions/{question_id}/comments` | `list_question_comments` | 可优化 | 【中】问题不存在也答 200 空页（同族 answers 那条答 404）；子回复 N+1 同 #6 |
| 36 | POST | `/questions/{question_id}/comments` | `create_question_comment` | 可优化 | 【中】完全不校验父级问题存在 → 可产出指向不存在题目的孤儿评论；`mentionedUserIds` 未校验同 #7 |
| 37 | DELETE | `/questions/{question_id}/comments/{comment_id}` | `delete_question_comment` | 可优化 | 【低】同 #8（整份 DTO 换作者 + `sender` 可能为 None → 500） |
| 38 | PUT | `/questions/{question_id}` | `update_question` | 可优化 | 【中】`type` 不做 `int()` 直接写 Integer 列 → 非法值 500（POST 同字段有保护）；响应 `createdAt` 与 `GET /questions/{id}` 的 `created_at` 不一致 |
| 39 | DELETE | `/questions/{question_id}` | `delete_question` | 可优化 | 【低】204 空体，而同族删除（评论、邀请）答 200 + JSON，前端要分支处理 |
| 40 | GET | `/questions/{question_id}/followers` | `list_question_followers` | 可优化 | 【中】本文件唯一**没有**鉴权的端点（见第三节）；只回 `[{"id":…}]`，调用方拿不到昵称头像 |
| 41 | PUT | `/questions/{question_id}/followers` | `follow_question_put` | 可优化 | 【低】与 #28 同一动作两套入口、两种结果（幂等 200 vs 400）；同样 `service._repo` 穿透 |
| 42 | PUT | `/questions/{question_id}/bounty` | `set_question_bounty` | 可优化 | 【中】`int(payload.get("bounty", 0))` 无保护（`questions.py:500`）→ 非数字 500；锁定业务规则使赏金只能升不能降、无法撤回 |
| 43 | POST | `/questions/{question_id}/attitudes` | `attitude_question` | 可优化 | 【低】与 #32/#33 重复；`user_attitude` 回显原样输入 |
| 44 | GET | `/questions/{question_id}/invitations` | `list_question_invitations` | 可优化 | 【低】`pageStart` 同 #1；读侧口径为「任何登录用户」是已文档化的产品决定（`questions.py:538-557`），不改 |
| 45 | POST | `/questions/{question_id}/invitations` | `invite_user_to_answer` | 可优化 | 【低】重复邀请答 400（应为 409 `ConflictError`）；无频率限制（任何登录用户都能批量邀请） |
| 46 | GET | `/questions/{question_id}/invitations/recommendations` | `get_invitation_recommendations` | 可优化 | 【中】「推荐」实际是 `list_profiles(limit).order_by(user_id.asc())` 的前 N 个用户（`user/repositories.py:416-425`），与题目无关、含提问者本人和已邀请者 |
| 47 | GET | `/questions/{question_id}/invitations/{invitation_id}` | `get_invitation_detail` | 暂无 | 绑定父级、鉴权、形状都正确，未发现可优化点 |
| 48 | DELETE | `/questions/{question_id}/invitations/{invitation_id}` | `delete_invitation` | 暂无 | 父级绑定 + 鉴权正确；不存在答 400 已被现有用例钉住，改 404 需另行决策（见第三节） |
| 49 | GET | `/tags` | `list_tags` | 可优化 | 【中】不传 `q` 时直接返回空页（列表接口列不出东西）；`page_start<0` 的 404 分支不可达（`Query(ge=0)` 已先答 422）；需要登录才能列标签 |
| 50 | GET | `/tags/{tag_id}` | `get_tag` | 暂无 | 单条查询 + 404 正确；响应键 `topic` 与路径 `/tags` 的错位是已文档化的产品口径（`routes/tags.py:1-20`） |
| 51 | POST | `/tags` | `create_tag` | 可优化 | 【低】`name` 不 trim（`" foo "` 与 `"foo"` 并存）；`tag.name` 无唯一索引（迁移里只有 PK），并发创建同一名字可落两行 |

## 采纳与反馈（`accept_feedback`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | POST | `/topics/{topic_id}/tasks/{task_id}/accept-card` | `create_accept_card` | 可优化 | 超时重试会撞上「已有待处理的验收卡」回 422，而不是把第一次那张卡还回来 |
| 2 | POST | `/topics/{topic_id}/tasks/{task_id}/push-fix` | `push_fix` | 暂无 | 观察式同步，幂等；GitHub 不通时转成 200 + reason 而不是 500，做法是对的 |
| 3 | POST | `/topics/{topic_id}/tasks/{task_id}/ready` | `mark_ready` | 可优化 | 路由刚 `require_in_room`，服务层又查同一条任务/话题一次 |
| 4 | POST | `/topics/{topic_id}/tasks/{task_id}/accept-card/describe` | `describe_card` | 可优化 | 同上重复解析；末尾那次 `describe` 又是一整套按卡的查询 |
| 5 | GET | `/accept-cards/{card_id}/deliverable` | `download_card_deliverable` | 可优化 | 不可变快照没有 ETag/条件请求；整份字节读进内存、无流式、无上限 |
| 6 | GET | `/topics/{topic_id}/accept-card` | `list_accept_cards` | 可优化 | 不带 `?task=` 时**无任何鉴权**；`describe` 按卡逐张查（N+1）；无分页、`task` 过滤在 Python |
| 7 | GET | `/topics/{topic_id}/pr-checks` | `topic_pr_checks` | 可优化 | 同样缺鉴权；每次轮询 2 次 GitHub 调用、每次新建连接；为找一张卡拉出整个话题的卡 |
| 8 | POST | `/accept-cards/{card_id}/approve` | `approve_card` | 可优化 | `body.approver_handle` 必填却被完全忽略；卡与话题各被查两遍 |
| 9 | POST | `/accept-cards/{card_id}/accept` | `accept_card` | 可优化 | `body.decided_by` 必填却被忽略（只用了 `head_sha`） |
| 10 | POST | `/accept-cards/{card_id}/reassign` | `reassign_card` | 可优化 | 复用 `AcceptCardCreate`，其中 8 个字段被静默忽略 |
| 11 | POST | `/accept-cards/{card_id}/reject` | `reject_card` | 可优化 | 同一次请求里同一个话题被解析 3 次、卡被取 2 次 |
| 12 | POST | `/accept-cards/{card_id}/void` | `void_card` | 可优化 | 同 #11 的重复查询一类 |
| 13 | POST | `/accept-cards/{card_id}/merge-anyway` | `merge_card_anyway` | 可优化 | 同 #11 的重复查询一类 |
| 14 | POST | `/accept-cards/{card_id}/auto-merge` | `set_auto_merge` | 可优化 | 同 #11 的重复查询一类 |
| 15 | POST | `/accept-cards/{card_id}/revoke` | `revoke_card` | 可优化 | 同 #11；请求体整个没被读过 |
| 16 | GET | `/feedback/meta` | `get_feedback_meta` | 暂无 | 静态词表 + 一次管理员判定，无问题 |
| 17 | GET | `/feedback/counts` | `get_feedback_counts` | 可优化 | 铃铛轮询一次发 5 条独立聚合查询（其中 hot 带 union + outer join） |
| 18 | POST | `/feedback/read` | `mark_feedback_read` | 暂无 | 一跳 upsert，幂等，写的就是游标 |
| 19 | GET | `/feedback/mine` | `list_my_feedback` | 可优化 | 每翻一页重算一整套 counts；「指派给我的」那一支逐行 `may_see`（1–2 条 SQL/行） |
| 20 | GET | `/feedback` | `list_feedback` | 可优化 | 每翻一页重算 8 条聚合；`sort` 不认识的取值被静默当成 `new`（admin 端同名参数会 400） |
| 21 | POST | `/feedback` | `create_feedback` | 暂无 | 24h 上限 + advisory lock + agent 拒之门外，写侧规则完整 |
| 22 | GET | `/feedback/{feedback_id}` | `get_feedback` | 暂无 | 可见性只有一处判据；详情里的计数全部按页批量取 |
| 23 | DELETE | `/feedback/{feedback_id}` | `delete_feedback` | 暂无 | 软删连带评论；404（看不见）与 403（不是你的）分得清 |
| 24 | GET | `/feedback/{feedback_id}/comments` | `list_feedback_comments` | 暂无 | 游标分页 + 每页一批；跨帖评论取不到 |
| 25 | POST | `/feedback/{feedback_id}/comments` | `create_feedback_comment` | 可优化 | 本域唯一没有配额/限流的写入口（反馈与提案都有） |
| 26 | DELETE | `/feedback/{feedback_id}/comments/{comment_id}` | `delete_feedback_comment` | 暂无 | `can_delete` 与删除用同一个判据 |
| 27 | POST | `/feedback/{feedback_id}/comments/{comment_id}/likes` | `like_feedback_comment` | 暂无 | `ON CONFLICT DO NOTHING`，回写后计数 |
| 28 | DELETE | `/feedback/{feedback_id}/comments/{comment_id}/likes` | `unlike_feedback_comment` | 暂无 | 同上，幂等 |
| 29 | POST | `/feedback/{feedback_id}/supports` | `support_feedback` | 暂无 | 已办完回 412，幂等，回写后计数 |
| 30 | DELETE | `/feedback/{feedback_id}/supports` | `unsupport_feedback` | 暂无 | 同上，幂等 |
| 31 | GET | `/topics/{topic_id}/feedback-proposals` | `list_feedback_proposals` | 可优化 | 拉全话题**所有带 meta 的消息**再在 Python 里筛，无上限、无 LIMIT |
| 32 | POST | `/topics/{topic_id}/feedback-proposals` | `propose_feedback` | 暂无 | 三道限流 + 话题级锁，顺序与理由都对 |
| 33 | POST | `/topics/{topic_id}/feedback-proposals/{block_id}/dismiss` | `dismiss_feedback_proposal` | 暂无 | 落一行、幂等 |
| 34 | POST | `/topics/{topic_id}/feedback-proposals/{block_id}/accept` | `accept_feedback_proposal` | 暂无 | 事务级 advisory lock + 卡上幂等标记，两处写在一个事务里 |

## 团队、成员与小组（`teams`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/admin/admins` | `list_admins` | 暂无 | `ETag` + `304` + `private, no-cache` + 一次读 memo，是本仓库条件请求的样板 |
| 2 | GET | `/admin/users` | `search_users` | 暂无 | 搜索在 SQL 里，`q` 1..64、`limit` ≤50，与 `/users?q=` 的「只搜一页」区分清楚 |
| 3 | POST | `/admin/admins` | `add_admin` | 暂无 | `INSERT ... ON CONFLICT DO NOTHING RETURNING` 幂等，`created` 说清是不是真加了 |
| 4 | DELETE | `/admin/admins/{target}` | `remove_admin` | 暂无 | `DELETE ... RETURNING` 幂等，根管理员 409 且判断权在服务里 |
| 5 | GET | `/groups` | `list_groups` | 可优化 | `groups/services.py:88` 每行一次 `count_members`（N+1）；`question_count`/`answer_count` 恒为 0 |
| 6 | POST | `/groups` | `create_group` | 可优化 | body 是裸 `dict`，`name`/`intro` 无长度上限，OpenAPI 里没有 schema |
| 7 | GET | `/groups/{group_id}` | `get_group` | 可优化 | 响应里的 `question_count`/`answer_count` 永远是 0（假数据） |
| 8 | PUT | `/groups/{group_id}` | `update_group` | 可优化 | 部分更新却用 PUT（应为 PATCH）；`group_profile` 行缺失时 `intro`/`avatarId` 静默丢弃 |
| 9 | DELETE | `/groups/{group_id}` | `delete_group` | 暂无 | 204，owner 校验在服务层 |
| 10 | GET | `/groups/{group_id}/members` | `list_group_members` | 可优化 | 游标分页每页 3 条 SQL（prev 那条多余）+ repo 里一段死分支 |
| 11 | POST | `/groups/{group_id}/members` | `join_group` | 暂无 | 常量 4 条 SQL，重复入队 409 |
| 12 | DELETE | `/groups/{group_id}/members` | `leave_group` | 暂无 | owner 不能退；非成员 409 |
| 13 | GET | `/groups/{group_id}/targets` | `list_group_targets` | 暂无 | 列表 + COUNT 两条，常量 |
| 14 | POST | `/groups/{group_id}/targets` | `create_group_target` | 可优化 | `startedAt` 非数字 → `TypeError` 冒到 500；无 `endedAt > startedAt` 校验 |
| 15 | GET | `/groups/{group_id}/targets/{target_id}` | `get_group_target` | 暂无 | `target.group_id != group_id` 校验到位，无跨组 IDOR |
| 16 | PUT | `/groups/{group_id}/targets/{target_id}` | `update_group_target` | 可优化 | 同 14 的 `fromtimestamp`；`attendanceFrequency` 任意字符串直存 |
| 17 | DELETE | `/groups/{group_id}/targets/{target_id}` | `delete_group_target` | 暂无 | 归属校验 + 角色校验 |
| 18 | GET | `/groups/{group_id}/questions` | `list_group_questions` | 暂无 | 只回 id 列表 + COUNT，常量 |
| 19 | POST | `/groups/{group_id}/questions` | `add_group_question` | 暂无 | `questionId` 校验 `int ≥ 1`，重复添加幂等 |
| 20 | DELETE | `/groups/{group_id}/questions/{question_id}` | `remove_group_question` | 暂无 | `(group_id, question_id)` 作用域正确 |
| 21 | POST | `/projects/{project_id}/members` | `add_member` | 暂无 | agent 座位原语；`require_manager` 门在服务里，不接受 body 里的 handle |
| 22 | GET | `/projects/{project_id}/members` | `list_members` | 可优化 | `members.py:65` 把 `list_for_project` 的 COUNT 查完就丢；`data.data` 嵌套 + `total` 名不副实 |
| 23 | DELETE | `/projects/{project_id}/members/{user_handle}` | `remove_member` | 暂无 | 席位先撤后删行，顺序对 |
| 24 | DELETE | `/projects/{project_id}/membership` | `leave_project` | 暂无 | 身份只从 resolver 来，`/membership` 命名避开了 `me` 被当 handle |
| 25 | POST | `/projects/{project_id}/invitations` | `invite_member` | 暂无 | 四条拒绝都在服务里，通知一条 |
| 26 | GET | `/projects/{project_id}/invitations` | `list_project_invitations` | 可优化 | `describe()` 逐行取项目行 + 邀请人资料（2N 条查询） |
| 27 | GET | `/me/invitations` | `list_my_invitations` | 可优化 | 同上，`members.py:170` |
| 28 | POST | `/invitations/{invitation_id}/respond` | `respond_to_invitation` | 暂无 | 只有被邀请人能答；重复答复 400 并说明已结束 |
| 29 | DELETE | `/invitations/{invitation_id}` | `revoke_invitation` | 暂无 | 撤回不删行，保留唯一记录 |
| 30 | GET | `/recruitment` | `list_recruitment_posts` | 可优化 | stealth 队的 OPEN 帖连同 `contact` 进匿名广场；游标按 `id`、排序按 `created_at`；过期帖照列 |
| 31 | PATCH | `/recruitment/{postId}` | `edit_recruitment_post` | 可优化 | `maxMembers`/`expiresAt` 无上下界 |
| 32 | DELETE | `/recruitment/{postId}` | `delete_recruitment_post` | 暂无 | 团队 ADMIN+ 校验在服务里 |
| 33 | POST | `/teams/{teamId}/recruitment` | `create_recruitment_post` | 可优化 | `title`/`content`/`contact`/`maxMembers` 无长度与数值约束；过去的 `expiresAt` 照收 |
| 34 | GET | `/teams/{teamId}/recruitment` | `list_team_recruitment_posts` | 可优化 | 无分页、无上限，一队的帖全量返回 |
| 35 | GET | `/teams` | `get_teams` | 可优化 | 每队 3 条 SQL（默认 20 队 = 61 条）；`pageStart` 非法值静默当 0 |
| 36 | GET | `/teams/my-teams` | `get_my_teams` | 可优化 | 每队 3 条 SQL，且没有分页；侧边栏轮询的就是它 |
| 37 | GET | `/teams/{teamId}` | `get_team` | 可优化 | `is_team_member` 的同一条判定在一个请求里查两遍 |
| 38 | GET | `/teams/by-handle/{handle}` | `get_team_by_handle` | 可优化 | 与 37 同一条 `_team_profile`，同样重复 |
| 39 | POST | `/teams/{teamId}/join` | `join_team` | 可优化 | 入队前后各算一遍 `join_status`；成功后又整份重算 profile |
| 40 | GET | `/teams/{teamId}/join-link` | `get_team_join_link` | 暂无 | 管理员门 + 一次 flush，`token_urlsafe(32)` |
| 41 | PATCH | `/teams/{teamId}/join-link` | `update_team_join_link` | 暂无 | 同上，复用 `join_link()` |
| 42 | POST | `/teams/{teamId}/join-link/reset` | `reset_team_join_link` | 暂无 | 重置即换 token，旧链接 404 |
| 43 | GET | `/team-invites/{token}` | `get_team_by_join_link` | 暂无 | 持链接可达、与 id 路分开的设计清楚；需登录是既有口径（测试钉住） |
| 44 | POST | `/team-invites/{token}/join` | `join_team_by_join_link` | 暂无 | 与 39 同一服务，同 39 的重复判定（低） |
| 45 | GET | `/teams/{teamId}/resource-quotas` | `get_team_resource_quotas` | 可优化 | 逐项目跑一次 `for_project` 全表聚合；项目列表查了两遍 |
| 46 | GET | `/teams/{teamId}/members` | `get_team_members` | 可优化 | 无分页；`queryRealNameStatus=true` 时每人一次查询（N+1） |
| 47 | POST | `/teams` | `create_team` | 暂无 | 重名 409 带结构化 `data`，handle 冲突有校验 |
| 48 | PATCH | `/teams/{teamId}` | `patch_team` | 暂无 | 路由门 ADMIN+ 与服务一致，回更新后的整份 team |
| 49 | DELETE | `/teams/{teamId}` | `delete_team` | 暂无 | 只 OWNER；软删成员 + 软删队伍 |
| 50 | DELETE | `/teams/{teamId}/members/{userId}` | `delete_team_member` | 暂无 | 服务内分级判权（owner/admin、admin 不能删 admin） |
| 51 | PATCH | `/teams/{teamId}/members/{userId}` | `patch_team_member_role` | 暂无 | 路由门是 ADMIN、服务要求 OWNER，服务更严不算漏洞，只是判定重复 |
| 52 | POST | `/teams/{teamId}/members` | `add_team_member_entry` | 可优化 | 路由直接调 `service._repo.add_member`（私有件），且不校验目标用户存在 |
| 53 | GET | `/teams/{teamId}/join-requests` | `list_team_join_requests` | 可优化 | `pageSize` 无上限（`le` 缺失）；列表 + COUNT 两条；`status` 白名单与 55 重复一份 |
| 54 | GET | `/teams/{teamId}/requests` | `list_team_requests_alias` | 可优化 | 与 53 逐字重复的别名（同一个 handler 转调） |
| 55 | GET | `/teams/{teamId}/invitations` | `list_team_invitations` | 可优化 | 同 53 |
| 56 | POST | `/teams/{teamId}/invitations` | `create_team_invitation` | 暂无 | 角色校验 + 服务内 `is_team_at_least_admin` |
| 57 | DELETE | `/teams/{teamId}/invitations/{invitationId}` | `cancel_team_invitation` | 暂无 | 置 CANCELED，不删行 |
| 58 | POST | `/teams/{teamId}/join-requests/{requestId}/approve` | `approve_team_join_request` | 暂无 | 204；服务内判权 + 团队锁校验 |
| 59 | POST | `/teams/{teamId}/requests/{requestId}/approve` | `approve_team_request_alias` | 可优化 | 与 58 重复的别名 |
| 60 | POST | `/teams/{teamId}/join-requests/{requestId}/reject` | `reject_team_join_request` | 暂无 | 同 58 |
| 61 | POST | `/teams/{teamId}/requests/{requestId}/reject` | `reject_team_request_alias` | 可优化 | 与 60 重复的别名 |

## 集成、连接器与安装（`integrations`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | POST | `/connector/auth/device/start` | `device_start` | 可优化 | 匿名可调、无限流，`device_auth_code` 只增不删（全仓无清理），响应缺 `expires_in` |
| 2 | POST | `/connector/auth/device/poll` | `device_poll` | 可优化 | 明文返回长效设备令牌，却没有 `Cache-Control: no-store`（RFC 6749 §5.1），也没有轮询限流 |
| 3 | GET | `/connector/auth/device/proposed-name` | `device_proposed_name` | 可优化 | `code_device_name` 不校验 TTL，过期 code 仍能读到设备名，与 `_live_code` 的语义不一致 |
| 4 | POST | `/connector/connect` | `device_connect` | 暂无 | 审批在登录后、幂等（`approve` 对已批 code 返回同一 device）、项目授权走 `authorize_project` |
| 5 | GET | `/connector/my/devices` | `my_devices` | 可优化 | `list_devices_by_owner` 1+2N 条 SQL；`_device_screens` 每台设备重扫一遍全部在线 screen |
| 6 | PATCH | `/connector/my/devices/{device_id}` | `rename_my_device` | 暂无 | 属主校验在 service（`_require_hosted_owned`），改名走非空校验，无越权面 |
| 7 | DELETE | `/connector/my/devices/{device_id}` | `unbind_my_device` | 暂无 | 属主校验 + 级联清理绑定；200 `{deleted:true}` 而非 204，是本项目惯例 |
| 8 | POST | `/connector/my/devices/{device_id}/teams` | `register_device_for_team` | 可优化 | 属主查询做了两遍，末尾 `_device_view(device)` 对可能为 `None` 的值加了 `type: ignore` |
| 9 | GET | `/connector/teams/{team_id}/devices` | `team_devices` | 可优化 | 单次请求 SQL 量 = `1+3N` + `Σ项目(2+3×设备数)`，团队越大越慢 |
| 10 | DELETE | `/connector/my/devices/{device_id}/teams/{team_id}` | `unregister_device_from_team` | 暂无 | 属主校验（`unassign_from_team` → `_require_hosted_owned`）后解绑，无问题 |
| 11 | GET | `/projects/{project_id}/github/connection` | `get_github_connection` | 暂无 | 登录 + `authorize_project`，只回 `{connected,repo,account}`，无敏感字段 |
| 12 | POST | `/projects/{project_id}/github/connect` | `connect_github_repo` | 可优化 | 逐个 installation 串行调 GitHub，且每次调用都新建 `httpx.AsyncClient`（每装一次就一次 TLS 握手） |
| 13 | GET | `/projects/{project_id}/github/install-url` | `get_github_install_url` | 暂无 | state 有 TTL + `single_use_state.reserve`，manager 才可调，是组内的正面样板 |
| 14 | GET | `/github/app/callback` | `github_app_install_callback` | 暂无 | 验签 state + 单次消费 + 用用户 token 反查 installation 归属 + 写权限校验，防重放已做全 |
| 15 | GET | `/connector/install.sh` | `install_script` | 暂无 | 无秘密、无业务逻辑，`_origin` 在生产由 `connector_public_base` 决定；纯生成脚本 |
| 16 | GET | `/connector/install.ps1` | `install_powershell` | 暂无 | 同 15，模板替换，无状态无查询 |
| 17 | GET | `/connector/claude/{version}/{platform}/{name}` | `download_claude` | 可优化 | 匿名可触发 40MB 级上游拉取并落盘，无鉴权无限流；未回 `X-Checksum-SHA256` |
| 18 | GET | `/connector/pi/{version}/{platform}/pi.tar.gz` | `download_pi` | 可优化 | 同 17，匿名可触发上游拉取 + 磁盘缓存 |
| 19 | GET | `/connector/toolchain/{tool}/{platform}/artifact` | `download_toolchain` | 可优化 | 同 17；另：`X-Checksum-SHA256` 只有它发，17/18/20 都不发，口径不一 |
| 20 | GET | `/connector/latest/{target}/{name}` | `download_binary` | 暂无 | target 白名单 + `binary_name` 比对，无上游拉取；`FileResponse` 自带 ETag/Last-Modified |
| 21 | GET | `/me/integrations` | `my_integrations` | 暂无 | 本人连接列表，量级小；`public()` 只对 feishu 行解密，未回 secret |
| 22 | POST | `/me/integrations/mail` | `connect_mail` | 可优化 | `_check` 在请求里串行做 DNS 解析 + IMAP 登录 + SMTP 登录（各 30s 超时），且期间占着连接 |
| 23 | POST | `/me/integrations/feishu` | `connect_feishu` | 可优化 | 同 22（换 tenant_token 拉取，20s 超时），建立连接时占池 |
| 24 | PATCH | `/me/integrations/{integration_id}` | `update_integration` | 可优化 | 改 folders/secret 会隐式触发一次上游探活（`update` → `_check`），同样占池 |
| 25 | POST | `/me/integrations/{integration_id}/check` | `check_integration` | 可优化 | 整个请求就是一次上游探活，池里的连接被压住直到探活返回 |
| 26 | DELETE | `/me/integrations/{integration_id}` | `delete_integration` | 暂无 | 属主校验后删除，回 `{deleted:id}`，无问题 |
| 27 | GET | `/me/integrations/{integration_id}/feishu/authorize` | `feishu_authorize` | 可优化 | state 只有 HMAC，无 `exp`、无一次性消费，链接永久有效（对照 13/14 的写法） |
| 28 | GET | `/integrations/feishu/callback` | `feishu_callback` | 可优化 | 同上；另外把上游错误原文截 200 字回显到跳转 query |
| 29 | GET | `/me/mail-drafts` | `my_drafts` | 可优化 | `.limit(100)` 静默截断，无 cursor/offset，也没有 `total` 之外的「还有没有下一页」信号 |
| 30 | POST | `/me/mail-drafts/{draft_id}/send` | `send_draft` | 可优化 | 无行锁/无幂等键，两个并发 `/send` 能各发一封；单请求最多 3 次串行 IMAP/SMTP 建连 |
| 31 | POST | `/me/mail-drafts/{draft_id}/discard` | `discard_draft` | 暂无 | 状态机校验 `drafted` 才能丢弃，单事务 |
| 32 | GET | `/projects/{project_id}/integrations` | `project_integrations` | 可优化 | `granted()` 是全表 `select(Integration)` 后在 Python 里筛 grants，等于把平台所有连接读进内存 |
| 33 | POST | `/integrations/{integration_id}/mail/search` | `mail_search` | 可优化 | IMAP 调用（≤30s）期间 DB 连接一直处于 idle-in-transaction |
| 34 | GET | `/integrations/{integration_id}/mail/messages/{uid}` | `mail_read` | 可优化 | 同 33 |
| 35 | GET | `/integrations/{integration_id}/mail/messages/{uid}/attachments/{index}` | `mail_attachment` | 可优化 | 同 33；另把第三方邮件的 `Content-Type` 原样回给浏览器，缺 nosniff/CSP |
| 36 | POST | `/integrations/{integration_id}/mail/drafts` | `mail_draft` | 可优化 | 同 33；且 handler 一次做完草稿写入 + Block + 通知三件事 |
| 37 | POST | `/integrations/{integration_id}/feishu/search` | `feishu_search` | 可优化 | 飞书调用（≤20s）期间连接 idle-in-transaction |
| 38 | GET | `/integrations/{integration_id}/feishu/docs/{document_id}` | `feishu_read` | 可优化 | 同 37（`read` 内部还会翻页 + 再取一次文档链接，往返更多） |
| 39 | POST | `/integrations/{integration_id}/feishu/docs` | `feishu_create` | 可优化 | 同 37（建文档 + 追加内容，两次上游往返都压在同一个事务里） |
| 40 | PATCH | `/integrations/{integration_id}/feishu/docs/{document_id}` | `feishu_edit` | 可优化 | 同 37（append + replace + read 三次往返） |
| 41 | GET | `/projects/{project_id}/mcp/servers` | `list_servers` | 暂无 | 成员校验 + `settings_view`，只回 `set: true/false` 不回值 |
| 42 | POST | `/projects/{project_id}/mcp/servers/{name}/connect` | `connect` | 暂无 | state 加密 + `exp` + `single_use_state`，PKCE S256，`resource` 参数齐全 |
| 43 | DELETE | `/projects/{project_id}/mcp/servers/{name}/connection` | `disconnect` | 暂无 | `with_for_update` + 尽力撤销上游 token，未连接回 404 合理 |
| 44 | PUT | `/projects/{project_id}/mcp/secrets/{name}` | `set_secret` | 暂无 | 只存密文、回 `ok(None)`，变量名必须在 `.mcp.json` 里出现过 |
| 45 | DELETE | `/projects/{project_id}/mcp/secrets/{name}` | `clear_secret` | 暂无 | 幂等删除；与 43 的 404 语义不一致，属小口径问题（见模块建议） |
| 46 | GET | `/mcp/oauth/client.json` | `client_metadata` | 暂无 | 按规范裸 JSON 不带信封，是 CIMD 文档要求，改不得 |
| 47 | GET | `/mcp/oauth/callback` | `callback` | 暂无 | state 加密且带 `exp` + 一次性 claim + RFC 9207 iss 校验，组内最完整 |
| 48 | POST | `/topics/{topic_id}/mcp/{name}` | `proxy` | 暂无 | 房间凭证 + project/topic 双重比对；把上游错误包成 200 `{error}` 是给 agent 看的既定约定 |
| 49 | GET | `/topics/{topic_id}/mcp/servers` | `room_servers` | 可优化 | `room_view` → `settings_view(fresh=True)` 每次强制读 forge 的 `.mcp.json` 并解密全部 secret |
| 50 | POST | `/webhooks/{topic_id}` | `receive_webhook` | 可优化 | 请求内同步重试最长 35s（GitHub 10s 就判投递失败），无 `X-GitHub-Delivery` 去重，失败也回 200 |

## 管理后台与健康（`admin`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/admin/feedback` | `list_admin_feedback` | 可优化 | 分页用 `page_start` 但当 **offset** 用，与 `design/common/parameters.yaml` 里「第一项的 id」两个意思；响应只给 `total`，没有 `has_more`/`next_start` |
| 2 | GET | `/admin/feedback/{feedback_id}` | `get_admin_feedback` | 暂无 | `visible_row` + `detail_of` 全部批量查（注释、笔记、头像、时间线各一条），无 N+1 |
| 3 | PATCH | `/admin/feedback/{feedback_id}` | `patch_admin_feedback` | 可优化 | 改 priority / assignee **不留任何记录**（只有 security 会写时间线） |
| 4 | POST | `/admin/feedback/{feedback_id}/status` | `set_admin_feedback_status` | 暂无 | 同状态幂等、时间线与状态同事务、响应前已 commit（`test_admin_feedback_commit.py` 钉着） |
| 5 | POST | `/admin/feedback/{feedback_id}/notes` | `create_admin_feedback_note` | 暂无 | 只增不改的一行，作者与时间在行上 |
| 6 | POST | `/admin/memory/migration/projects/{project_id}/dry-run` | `dry_run_migration` | 可优化 | 几分钟的模型循环跑在请求里（无总时限），且无重复提交保护——连点两次付两次模型钱 |
| 7 | GET | `/admin/memory/migration/projects/{project_id}/plans` | `list_migration_plans` | 可优化 | 无 limit；为只返回计数却把整行（含 `report`、`files[].content` 全文 JSONB）读进内存 |
| 8 | GET | `/admin/memory/migration/plans/{plan_id}` | `get_migration_plan` | 可优化 | `files_preview` 把每个文件的**全文**塞进响应，无上限、无 ETag |
| 9 | POST | `/admin/memory/migration/plans/{plan_id}/approve` | `approve_migration` | 暂无 | 只认 `settings.memory_migration_reviewer`，判断在服务层且从路由体可达（真的挡得住） |
| 10 | POST | `/admin/memory/migration/plans/{plan_id}/apply` | `apply_migration` | 可优化 | **操作人丢了**：`apply(plan_id, by=admin)` 的 `by` 函数体里一次都没用，表也没有 `applied_by` 列 |
| 11 | GET | `/admin/memory/reads` | `memory_body_reads` | 可优化 | `blocks` 上没有任何以 `created_at` 打头的索引，不带 `project_id` 时是全表顺序扫 |
| 12 | GET | `/admin/gateway/models` | `list_models` | 暂无 | 网关答案 15s 缓存 + 一次 usage 窗口，形状与契约 §3.1 一致 |
| 13 | GET | `/admin/gateway/models/{name}` | `model_detail` | 可优化 | `_platform_usage` 为取一行而跑窗口内 top-200 的 `by_model` 聚合（还要排序），且这次读**不在** 15s 缓存里 |
| 14 | POST | `/admin/gateway/models` | `create_model` | 暂无 | 202/校验/审计/缓存失效都齐（`add` → `_record` → `_after_write`） |
| 15 | PATCH | `/admin/gateway/models/{name}` | `update_model` | 暂无 | 合并后就地校验不变式（`_require_price_when_selectable`），config 模型 400 拒 |
| 16 | DELETE | `/admin/gateway/models/{name}` | `delete_model` | 暂无 | 审计含 `before`/`after`，失败也落行 |
| 17 | POST | `/admin/gateway/models/{name}/blocked` | `set_model_blocked` | 暂无 | 同上，且 config 模型拒绝停用 |
| 18 | GET | `/admin/gateway/projects` | `list_projects` | 可优化 | `ProjectService.list_all()` 全平台项目一页吐完，无分页；加载页 = 全部项目 × 每项一行 |
| 19 | PUT | `/admin/gateway/projects/{project_id}/budget` | `set_project_budget` | 暂无 | `project_id` 走 `uuid.UUID` 解析、审计带 before/after、网关错误分 503/502 |
| 20 | GET | `/admin/gateway/audit` | `audit_log` | 可优化 | 只有 `limit`，没有游标/offset：第 200 条之前的记录**永远取不到**；也不能按 actor/action 过滤 |
| 21 | GET | `/admin/spaces` | `list_space_reviews` | 可优化 | 信封缺 `message`（`{"code":200,"data":{...}}`，全仓其它地方都走 `ok()`）；响应无 `total` |
| 22 | POST | `/admin/spaces/{space_id}/review` | `review_space` | 可优化 | 同一个信封缺 `message` 的问题；审计留在 Space 行上（`reviewed_by`/`reviewed_at`），这一点没问题 |
| 23 | GET | `/admin/stats/feedback` | `feedback_stats` | 可优化 | 为了取一个 `unread`，把 `counts(handle, is_admin=True)` 的七八条查询全跑了（只 `.get("unread")`） |
| 24 | GET | `/admin/stats/usage` | `usage_stats` | 可优化 | 同一窗口对 `resource_usage` 发了 6–7 条聚合；`totals` 与 `series` 可合并成一条 |
| 25 | GET | `/admin/stats/platform` | `platform_stats` | 可优化 | `_health_snapshot` 里每次新建 Redis 连接（每次连、每次 `aclose`），见 health 那三条 |
| 26 | GET | `/admin/stats/performance` | `performance_stats` | 可优化 | 每个请求都反射遍历整张路由表（`_http_endpoints`），而路由表启动后不变 |
| 27 | GET | `/admin/stats/pipeline` | `pipeline_stats` | 暂无 | 十几个 `count(*)`，但都在 `accept_cards`/`questions` 这类小表上，代价可接受（模式见模块级第 4 条） |
| 28 | GET | `/admin/stats/product` | `product_stats` | 暂无 | 三组都是按天 GROUP BY，窗口必填，无 N+1 |
| 29 | GET | `/admin/stats/integrations` | `integrations_stats` | 暂无 | 四个 `count(*)`（凭据表量级），无窗口是刻意的 |
| 30 | POST | `/admin/subscriptions/device-flows` | `start_device_flow` | 暂无 | 「一座一订阅」409 在服务层兑现，审计落 `subscription.start` |
| 31 | POST | `/admin/subscriptions/device-flows/{flow_id}/poll` | `poll_device_flow` | 可优化 | 前端每次轮询都 `SELECT … FOR UPDATE` + 写 `flow_last_poll_at` + 提交，被节流的那次也照写 |
| 32 | POST | `/admin/subscriptions/device-flows/{flow_id}/cancel` | `cancel_device_flow` | 暂无 | 行锁 + `subscription.cancel` 审计 |
| 33 | GET | `/admin/subscriptions` | `list_subscriptions` | 暂无 | 无分页，但这张表天然是「一座一订阅」的个位数量级（`list_all` 仍是全量，见模块级第 5 条） |
| 34 | POST | `/admin/subscriptions/{subscription_id}/refresh` | `refresh_subscription` | 暂无 | 行锁内完成，三类结局各自落审计 |
| 35 | GET | `/admin/subscriptions/{subscription_id}/quota` | `subscription_quota` | 暂无 | 传输错误回旧快照（`stale: true`），无快照才 503 |
| 36 | PATCH | `/admin/subscriptions/{subscription_id}/upstream-model` | `update_upstream_model` | 暂无 | 显式 null 与「没提」靠 `model_fields_set` 区分，审计落 failed |
| 37 | DELETE | `/admin/subscriptions/{subscription_id}` | `revoke_subscription` | 暂无 | 终态必落库、网关失败只影响这次响应，审计落 failed |
| 38 | GET | `/ai/quota` | `get_ai_quota` | 可优化 | 读路径会 INSERT（懒建额度行），而 `user_ai_quota.user_id` **没有唯一约束**——并发首次访问会插两行，之后每次 `scalar_one_or_none()` 直接 500 |
| 39 | GET | `/ai/models` | `list_ai_models` | 待确认 | 全模块唯一**没有**任何鉴权依赖的端点（静态列表，风险低，但同模块不一致） |
| 40 | GET | `/ai/conversations` | `list_conversations` | 可优化 | 参数名驼峰 `pageStart`/`pageSize`（本模块外一律下划线），且这里的 `pageStart` 是**游标 id**，与 `/admin/feedback` 的同名参数（offset）语义相反 |
| 41 | POST | `/ai/conversations` | `create_conversation` | 可优化 | `payload: dict = Body(...)` 无 schema；`modelId` 收下即丢（死参数）；无幂等键，重试即多一条会话 |
| 42 | GET | `/ai/conversations/{conversationId}` | `get_conversation` | 可优化 | 一次把该会话**全部**消息塞进响应，无 limit、无游标 |
| 43 | DELETE | `/ai/conversations/{conversationId}` | `delete_conversation` | 待确认 | 204 空体（符合 GitHub DELETE 惯例）与本仓 `{"code","message","data"}` 信封冲突——要留哪种得定 |
| 44 | PATCH | `/ai/conversations/{conversationId}` | `update_conversation` | 可优化 | `title` 只判「是不是 None」：传 dict/超长串会直达 DB 列；`payload: dict` 无长度校验 |
| 45 | POST | `/ai/chat` | `chat_with_ai` | 可优化 | 每个请求都新建一个 `openai.AsyncOpenAI`（新的 httpx 连接池 + TLS 握手）；无显式超时；历史消息不截断 |
| 46 | POST | `/projects/{project_id}/alerts` | `create_notification` | 可优化 | 广播按收件人逐条 `user_by_handle` + 逐行 flush+refresh（30 人 ≈ 90 条语句）；agent 重发无幂等保护 |
| 47 | GET | `/projects/{project_id}/alerts` | `list_notifications` | 可优化 | 无分页；`total` 恒等于这一页的条数（`page(items, len(items))`），一旦加上 limit 就会说谎 |
| 48 | GET | `/projects/{project_id}/inbox` | `project_inbox` | 可优化 | 同上，`list_inbox` 无 LIMIT |
| 49 | GET | `/projects/{project_id}/alerts/unread-count` | `notifications_unread_count` | 暂无 | 服务端一条 `count(*)`，正是为了不拉整份列表 |
| 50 | POST | `/projects/{project_id}/alerts/read-all` | `mark_all_notifications_read` | 可优化 | 把未读**全部行**读进 ORM 再逐行置 `read=True`，应是一条 `UPDATE` |
| 51 | POST | `/alerts/{notification_id}/read` | `mark_notification_read` | 暂无 | `get_or_404` 两次调的是 `session.get`（identity map 命中，不重复发 SQL），授权落在收件人上 |
| 52 | POST | `/alerts/{notification_id}/feedback` | `set_notification_feedback` | 暂无 | 同上 |
| 53 | POST | `/alerts/{notification_id}/resolve` | `resolve_notification` | 暂无 | `resolved_at` 让它天然幂等；决定记在验证过的调用者名下 |
| 54 | GET | `/spaces/{space_id}/dashboard` | `space_dashboard` | 可优化 | 单请求 ≈ 11N+1 条查询：门里每项目最多 6 条、板子上每项目 5 条（含一条**每项目一条 UPDATE**），且 `list_ids_for_space_tasks` 跑了两次 |
| 55 | GET | `/projects/{project_id}/members/{user_handle}/summary` | `member_summary` | 可优化 | 为筛出两小段，把整个项目的非私密话题树全量载入内存；`waiting_on_you` 走无 LIMIT 的 `list_inbox` |
| 56 | GET | `/projects/{project_id}/usage` | `project_usage` | 暂无 | 一条 `_agg`，走 `project_id` 索引 |
| 57 | GET | `/projects/{project_id}/credits` | `project_credits` | 暂无 | 两条查询（grants + team），字段注释解释了 `source_task_id` 为何是 int |
| 58 | GET | `/projects/{project_id}/contributions` | `contributions` | 可优化 | 对项目**全部历史** block 做 GROUP BY，无时间窗；`by_author` 的键数随作者数无界增长 |
| 59 | GET | `/users/{handle}/profile` | `user_profile` | 暂无 | 系列查询都批量、有窗口，`understanding` 只在本人页出 |
| 60 | GET | `/users/{handle}/topics` | `user_topics` | 暂无 | `limit` 有上界（≤50）、时间窗半开、日期顺序有校验 |
| 61 | DELETE | `/users/me/understanding/{entry_id}` | `forget_understanding` | 暂无 | 不是自己的、不存在的、别人的一律同一个 404（不可枚举），无 IDOR |
| 62 | GET | `/healthz` | `health_check` | 暂无 | 检查的是「路由模块有没有挂载失败」，形状固定，`{"status":"ok"}` |
| 63 | GET | `/health/detailed` | `detailed_health_check` | 可优化 | 每次调用新建一个 Redis 客户端（`from_url` + `ping` + `aclose`）；一次 DB 会话另开。它是 `/readyz` 与 `/admin/stats/platform` 的公共下游 |
| 64 | GET | `/metrics` | `get_metrics` | 可优化 | 无任何鉴权，公开导出按路由的请求量/耗时与平台计数（业界惯例是只在内网暴露） |
| 65 | GET | `/readyz` | `readiness_check` | 暂无 | 只有 `_REQUIRED_CHECKS` 能把它压成 503，判定与 `/health/detailed` 同源（代价见 63） |

## 内容、文件与知识（`content`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | POST | `/attachments` | `upload_attachment` | 可优化 | 上传时把整个文件读进内存只为量 size（`services.py:64-65`），且路由层没有大小上限 |
| 2 | GET | `/attachments/{attachmentId}` | `get_attachment_detail` | 可优化 | 一次查询 + 一次题域判据，本身干净；但 `to_dict` 把 `meta.uploaderId`（内部用户 id）一并给了任何登录读者 |
| 3 | GET | `/attachments/{attachmentId}/download` | `download_attachment` | 可优化 | 同一行被读两次（`routes:162` 判权 + `services.download:147` 再读一次），响应整体进内存 |
| 4 | DELETE | `/attachments/{attachmentId}` | `delete_attachment` | 暂无 | uploader 判据在服务层（`services.py:165`），存储删失败时的「查一次再决定」是对的 |
| 5 | POST | `/discussions` | `create_discussion` | 可优化 | 刚建的帖子 reaction 摘要必然为空，却固定花 3 条查询；body 是裸 `dict` |
| 6 | GET | `/discussions` | `list_discussions` | 可优化 | 一页 20 条 ≈ 340 条 SQL：reaction / 子回复 / 被 @ 人三重 N+1 |
| 7 | GET | `/discussions/reactions` | `list_reaction_types` | 可优化 | 本组唯一不鉴权的接口；GET 里带 INSERT（`ensure_defaults`），每调一次先 COUNT 一次 |
| 8 | GET | `/discussions/{discussionId}` | `get_discussion` | 可优化 | 子回复被取两遍（`services.py:272` 的 examples + `routes:313` 的分页），reaction 逐行算 |
| 9 | PATCH | `/discussions/{discussionId}` | `patch_discussion` | 可优化 | 同一行 `get_by_id` 三次（`routes:148` → `services:158` → `services:169`），每次还带一条 mention 查询 |
| 10 | GET | `/discussions/{discussionId}/sub-discussions` | `list_sub_discussions` | 可优化 | 与 8 走同一段 `list_discussions`，逐行 reaction 摘要 |
| 11 | DELETE | `/discussions/{discussionId}` | `delete_discussion` | 可优化 | 为了拿 `sender.id` 建了整个 DTO（含子树与 reaction），约 12 条 SQL |
| 12 | POST | `/discussions/{discussionId}/reactions/{reactionTypeId}` | `toggle_reaction` | 可优化 | 每次 toggle 先跑 `ensure_defaults` 的 COUNT；toggle 语义非幂等 |
| 13 | DELETE | `/discussions/{discussionId}/reactions/{reactionTypeId}` | `remove_reaction` | 可优化 | 同 12（同一段 `reaction_services.toggle/remove`） |
| 14 | POST | `/docs/dev-access` | `grant_dev_access` | 暂无 | 签 httpOnly + SameSite=strict 的短票，仅平台管理员 |
| 15 | GET | `/docs/dev-access/check` | `check_dev_access` | 暂无 | 管理员名单有 60s 缓存（`access.AdminSet`），每个静态文件都查一次也不是问题 |
| 16 | POST | `/docs/ask` | `ask` | 可优化 | `_refuse` 的错误体是 `{code,message}`，与平台统一错误体（多一个 `error{}`）不一致 |
| 17 | POST | `/docs/agent/search` | `agent_search_docs` | 暂无 | 索引 10 分钟缓存（`retrieval.IndexSource`），命中即内存 |
| 18 | POST | `/docs/agent/read` | `agent_read_docs` | 可优化 | 同一份静态文档每次请求都重新 HTTP 拉一遍 —— 索引有缓存，页面没有 |
| 19 | POST | `/knowledge` | `create_knowledge` | 暂无 | 建一条 4 条查询组装 DTO，校验顺序合理 |
| 20 | GET | `/knowledge` | `list_knowledge` | 可优化 | 前端发的 `sort_by`/`sort_order` 被静默忽略；仓库层同一组过滤条件抄了两份 |
| 21 | GET | `/knowledge/{knowledgeId}` | `get_knowledge_by_id` | 暂无 | 单行 + 团队判据；批量 DTO 组装 |
| 22 | PATCH | `/knowledge/{knowledgeId}` | `patch_knowledge` | 可优化 | `name` 没有空白与长度校验，而列是 `String(255)`；超长直接 500 |
| 23 | DELETE | `/knowledge/{knowledgeId}` | `delete_knowledge` | 可优化 | 回包缺 `data` 键，同模块其余全部三键齐全（契约测试也这么断言） |
| 24 | POST | `/knowledge/{knowledgeId}/upvote` | `upvote_knowledge` | 可优化 | SELECT-再-INSERT：并发下撞唯一约束 `uq_knowledge_upvote` 变成 500 |
| 25 | DELETE | `/knowledge/{knowledgeId}/upvote` | `remove_upvote_knowledge` | 暂无 | 幂等（不存在即返回），权限走同一条 `_ensure_team_member` |
| 26 | GET | `/legal/documents` | `list_documents` | 可优化 | 内容随部署固定、进程内已 `@cache`，却没有任何 ETag/304 |
| 27 | GET | `/legal/documents/{document}` | `get_document` | 可优化 | 回包里本来就有 `sha256`，拿它当 ETag 是零成本的 |
| 28 | GET | `/legal/documents/{document}/versions/{version}` | `get_document_version` | 可优化 | 同 27；`version` 路径参数无格式约束，靠字典查不到来 404 |
| 29 | GET | `/users/me/consents` | `get_my_pending_consents` | 暂无 | 单查询 + 内存文档表，只给本人 |
| 30 | POST | `/users/me/consents` | `accept_documents` | 暂无 | 先校验版本再落库，响应前显式 `commit`（`legal.py:142`），是这组里最稳的一处 |
| 31 | GET | `/material-bundles` | `list_material_bundles` | 可优化 | 每次都把全表 id 拉进内存；`page_start` 不在集合里时元数据与 items 互相矛盾 |
| 32 | GET | `/material-bundles/{bundle_id}` | `get_material_bundle` | 可优化 | 每个 material 一次 `get_by_id`（`services.py:226-229`），N+1 |
| 33 | POST | `/material-bundles` | `create_material_bundle` | 可优化 | 每个 id 一次存在性查询 + 每个 id 一次 INSERT |
| 34 | PATCH | `/material-bundles/{bundle_id}` | `update_material_bundle` | 可优化 | 差集里每个 id 各一次查询（`add/remove_material_to_bundle` 各自先 SELECT） |
| 35 | DELETE | `/material-bundles/{bundle_id}` | `delete_material_bundle` | 暂无 | creator 判据 + 单次删除 |
| 36 | POST | `/materials` | `upload_material` | 可优化 | `await file.read()`（`routes/materials.py:44`）整体进内存，最坏 100MB/请求 |
| 37 | GET | `/materials/{material_id}` | `get_material_detail` | 待确认 | 任何登录用户可读任意材料的元数据与 URL —— 服务层注释明说这是设计（`services.py:71-80`），要不要收紧是产品决定 |
| 38 | DELETE | `/materials/{material_id}` | `delete_material` | 暂无 | uploader 判据在服务层（`services.py:119`） |
| 39 | GET | `/memory` | `list_memory` | 可优化 | 无分页无上限，按 scope 前缀全量返回，`pageSize` 恒等于行数 |
| 40 | DELETE | `/memory/{entry_id}` | `delete_memory` | 暂无 | 越权一律答「不存在」，判据与列表同一处，做对了 |
| 41 | GET | `/memory/files` | `list_memory_files` | 暂无 | 单查询；「一个作用域一次性给全」是写明的产品决定 |
| 42 | PUT | `/memory/files` | `write_memory_file` | 可优化 | 与 43 有约 30 行逐字重复的 body 解析（`memory_files.py:167-197` vs `237-258`） |
| 43 | POST | `/memory/files/delete` | `delete_memory_file` | 可优化 | 动词进路径；同一件事 `DELETE /memory/files` 更合适 |
| 44 | GET | `/topics/{topic_id}/files/raw` | `room_file_raw` | 可优化 | 已经在回 `X-Cheese-Version`（内容哈希），却不认 `If-None-Match`：每次 pull 全量回 |
| 45 | GET | `/topics/{topic_id}/files/revisions` | `list_file_revisions` | 可优化 | 某路径的**全部**历史一次返回，无 limit 无分页 |
| 46 | GET | `/topics/{topic_id}/files/revisions/{revision_id}/raw` | `file_revision_raw` | 暂无 | 单行 + `room_id` 归属比对（`room_files.py:273`） |
| 47 | POST | `/topics/{topic_id}/files/revisions/{revision_id}/restore` | `restore_file_revision` | 暂无 | 恢复本身也落成一条新 revision，可撤销；提交时机正确 |
| 48 | POST | `/topics/{topic_id}/files/copy` | `copy_into_room` | 暂无 | 重名先拒、写库前 `commit`、路径过 `_room_path` |
| 49 | GET | `/topics/{topic_id}/files/templates` | `list_templates` | 暂无 | 内存模板目录，无 DB |
| 50 | POST | `/topics/{topic_id}/files/new` | `new_from_template` | 暂无 | 校验模板后缀 + 重名，路径同 48 |
| 51 | GET | `/topics/{topic_id}/files/editor` | `open_in_editor` | 暂无 | 每次现签一份短票；未启用时 `enabled:false` 也走 200 信封，一致 |
| 52 | GET | `/office-editor/files/{link}` | `editor_fetches_file` | 暂无 | 链接 token 即凭据（写明），字节整体进内存 —— 见 M4 的条件请求建议 |
| 53 | POST | `/office-editor/callback/{link}` | `editor_saves_file` | 暂无 | 验签 + `saved_by_session` 幂等 + 冲突另存，并发处理是全模块最讲究的一处 |

## 实时通道、设备与杂项（`realtime`）

| # | method | path | handler | 结论 | 一句话 |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/topics/{topic_id}/agent/control` | `control_state` | 暂无 | 只读本地镜像文件（`harness/claude_code/runtime.py:139`），鉴权齐、无 DB 往返；断线无影响（一次性响应）。 |
| 2 | POST | `/topics/{topic_id}/agent/control` | `control` | 可优化 | `runtime.control()` 无超时；`except Exception` 把一切失败变 200；`request_id` 只回显不做幂等。 |
| 3 | POST | `/projects/{project_id}/agent-credential` | `issue_agent_credential` | 可优化 | `issue()` 与 `agent_handle()` 各自 `ProjectRepository.get()`，同一次请求读同一个项目两遍。 |
| 4 | GET | `/projects/{project_id}/agent-credential` | `get_agent_credential_status` | 暂无 | 一次 `current_epoch`，无敏感字段泄漏（只回 epoch）。 |
| 5 | DELETE | `/projects/{project_id}/agent-credential` | `revoke_agent_credentials` | 暂无 | 一次写 + 自增 epoch，语义幂等；断线重发安全。 |
| 6 | GET | `/agent-types` | `list_agent_types` | 可优化 | 每次请求从 `preset_types()` 重建并 `model_dump` 全部预设；纯常量，`lru_cache` 即可。 |
| 7 | POST | `/avatars` | `create_avatar` | 可优化 | `await avatar.read()` 无上限（内存 DoS）；手写信封缺 `warnings` 通道。 |
| 8 | GET | `/avatars/` | `get_available_avatars` | 可优化 | 与 `/avatars/predefined/id` 完全重复；`type` 只有一个合法值；手写信封。 |
| 9 | GET | `/avatars/default` | `get_default_avatar` | 可优化 | `created_at` 为空时 `Last-Modified` 回退成 `now()`，`If-Modified-Since` 永远失效。 |
| 10 | GET | `/avatars/default/id` | `get_default_avatar_id` | 可优化 | 手写信封（`{"code","message","data"}` 但绕过 `ok()`）。 |
| 11 | GET | `/avatars/predefined/id` | `get_predefined_avatar_ids` | 可优化 | 与 #8 重复，同一条数据两个入口，两个都手写信封。 |
| 12 | GET | `/avatars/{avatar_id}` | `get_avatar_by_id` | 暂无 | 文件字节路径无穿越（id 是 `int`），ETag/304 已正确实现。 |
| 13 | GET | `/awaiting-me` | `list_awaiting_me` | 可优化 | 无分页：一次拉「我能看见的全部项目」的全部活与房间，在内存里排序。 |
| 14 | POST | `/backend-errors` | `report_backend_errors` | 暂无 | 作用域令牌优先于 body 的判定正确，去重/限额在下游 ；断线无影响。 |
| 15 | PATCH | `/blocks/{block_id}` | `edit_block` | 暂无 | 编辑后 commit 再广播，帧不早于持久化；重连靠 `GET /blocks` 补。 |
| 16 | POST | `/blocks/{block_id}/reactions` | `toggle_reaction` | 暂无 | commit 后再 `publish`（`blocks.py:96-100`），顺序正确；reaction 帧不缓冲、靠重拉补齐。 |
| 17 | POST | `/topics/{topic_id}/execution/{resource_id}` | `execute` | 暂无 | 本仓库最讲究的一条：跨远端调用前先 commit 释放连接池槽位（`execution.py:162-176`），派发记录 + 未结清语义完整。 |
| 18 | POST | `/topics/{topic_id}/execution/session-{resource_id}` | `execute` | 暂无 | 与 #17 是同一个 handler 的两个装饰器（`execution.py:49-52`），清单按两行列出正确。 |
| 19 | POST | `/fetch` | `read_url` | 可优化 | 服务端替调用方发任意 URL 的 GET，**无任何 SSRF 防护**（`fetch/layers.py:90-95,133-138`）。 |
| 20 | GET | `/sandbox/forge-token` | `sandbox_forge_token` | 可优化 | 每次调用都新签一个安装令牌并另查一次权限（两次 GitHub 往返），无缓存。 |
| 21 | POST | `/frontend-errors` | `report_frontend_errors` | 可优化 | 完全无鉴权即可往任意项目时间线写 event block；逐条 insert，无批量。 |
| 22 | GET | `/users/me/github-account/authorize-url` | `get_github_account_authorize_url` | 暂无 | state 先 reserve 再签发，失败即拒（`github_account_link.py:72-81`），语义正确。 |
| 23 | GET | `/users/me/github-account/callback` | `github_account_link_callback` | 暂无 | 一次性 state + 每次出口都记日志；失败也 302，不泄 `return_project_id`。 |
| 24 | POST | `/llm/admission` | `admission` | 待确认 | 每轮模型调用前必打的闸，本请求内做 3+ 次 DB 读；但「一控点」是刻意设计（结论 46），缓存是否安全需设计者判断。 |
| 25 | GET | `/connector/my/devices/{device_id}/directories` | `list_directories` | 可优化 | 返回裸对象而非项目信封；先把设备查一遍再复查一遍（`local_dirs.py:92` + `:152`）。 |
| 26 | POST | `/connector/my/devices/{device_id}/directories` | `grant_directory` | 暂无 | 授权记录先落库再尽力下发（`local_dirs.py:183-187`），设备离线不阻塞，顺序正确。 |
| 27 | DELETE | `/connector/my/devices/{device_id}/directories/{grant_id}` | `revoke_directory` | 暂无 | 归属校验含 `grant.device_id != device_id`，撤销后立即下发，正确。 |
| 28 | GET | `/connector/my/access-log` | `my_access_log` | 可优化 | 裸对象；只有 `limit` 没有游标，翻不到更早的记录。 |
| 29 | GET | `/projects/{project_id}/machines` | `list_machines` | 可优化 | `list_for_project` 对每台「在动/过期」的机器串行打一次 MicroCloud（`machine/services.py:732-746`）；项目又被读两遍。 |
| 30 | DELETE | `/projects/{project_id}/machines/{machine_row_id}` | `delete_machine` | 暂无 | 归属校验显式（`machines.py:120-123`），异步删除语义清楚。 |
| 31 | POST | `/projects/{project_id}/machines/{machine_row_id}/{operation}` | `change_machine_power` | 暂无 | `operation` 收敛为 `Literal["suspend","resume"]`，非自由动词；鉴权走 `mutate=True` 分支。 |
| 32 | GET | `/market/pools` | `list_pools` | 可优化 | 纯配置推导出的静态目录，每次重算且无 ETag/条件请求。 |
| 33 | GET | `/market/nodes` | `list_nodes` | 暂无 | 一次内存快照 + `active_work_count()`，无查询。 |
| 34 | GET | `/notifications/unread-count` | `get_unread_count` | 暂无 | 单条 `COUNT`，走接收人索引。 |
| 35 | GET | `/notifications` | `list_notifications` | 可优化 | 逐行 `build_notification_dto` → 每行独立解析实体（N×最多 3 次查询）；每页还多一次 `COUNT(*)`。 |
| 36 | PATCH | `/notifications` | `bulk_update_notifications` | 暂无 | 批量改已读按 `user_id` 收窄，越权面已封。 |
| 37 | PUT | `/notifications/status` | `set_collective_status` | 暂无 | 与 GitHub `PUT /notifications`（全部已读）语义一致。 |
| 38 | GET | `/notifications/{notification_id}` | `get_notification` | 暂无 | 归属收窄正确（按 `user_id` 查）。 |
| 39 | PATCH | `/notifications/{notification_id}` | `update_notification_status` | 可优化 | UPDATE 之后再 SELECT 再解析实体，共 3~5 次往返；`UPDATE ... RETURNING` 一步可回。 |
| 40 | DELETE | `/notifications/{notification_id}` | `delete_notification` | 暂无 | 204 无正文，与 GitHub 一致；但与本项目别处的删除形状不同（见第三节）。 |
| 41 | POST | `/topics/{topic_id}/preview-session` | `preview_session` | 暂无 | 短时 grant + `Cache-Control: no-store`；重连靠重新签发。 |
| 42 | GET | `/projects/{project_id}/environment` | `get_environment` | 可优化 | `access()` 固定 4 次查询（项目、用户、`manages`、`roster`），房间列表另一次；无 ETag。 |
| 43 | PUT | `/projects/{project_id}/environment` | `save_environment` | 可优化 | `access()` 已读过 Project，随后又 `select(...).with_for_update()` 读第二遍；且只 `flush()`。 |
| 44 | GET | `/projects/{project_id}/environment/rooms/{topic_id}` | `get_room_environment` | 可优化 | GET 里同步等一次设备 `hub.exec`（`device_provider.py:501`，`timeout=10`）。 |
| 45 | POST | `/projects/{project_id}/environment/rooms/{topic_id}/apply` | `apply_environment` | 暂无 | 锁内 commit 再放行下一轮；设备调用前显式 commit（`project_environment.py:220`）。 |
| 46 | GET | `/projects/{project_id}/environment/recovery/rooms/{topic_id}` | `inspect_recovery` | 可优化 | 同 #44：只读接口里等设备往返。 |
| 47 | POST | `/projects/{project_id}/environment/recovery/rooms/{topic_id}` | `repair_environment` | 暂无 | 先 commit 再 `environment_status`（`:355`），`attempt` 对不上即拒，竞态处理到位。 |
| 48 | GET | `/projects/{project_id}/skills` | `list_skills` | 可优化 | 列表返回每条技能的全文 `steps`/`files`，无分页。 |
| 49 | POST | `/topics/{topic_id}/skills` | `create_skill` | 暂无 | 分身后补一条 event block 再 commit，顺序正确。 |
| 50 | GET | `/skills/{skill_id}` | `get_skill` | 暂无 | 一次 get + 一次 revisions。 |
| 51 | PATCH | `/skills/{skill_id}` | `update_skill` | 暂无 | `exclude_unset=True` 语义正确。 |
| 52 | POST | `/skills/{skill_id}/confirm` | `confirm_skill` | 暂无 | 人/agent 判据 `_is_person` 明确。 |
| 53 | POST | `/skills/{skill_id}/revisions/{revision}/restore` | `restore_skill` | 暂无 | 同上。 |
| 54 | DELETE | `/skills/{skill_id}` | `delete_skill` | 暂无 | 返回 `ok({"deleted":...})`，与 #40 的 204 不一致 —— 归第三节。 |
| 55 | GET | `/push/key` | `push_public_key` | 暂无 | 公开密钥，静态；如实返回 `null` 而不是报错。 |
| 56 | PUT | `/push/subscriptions` | `save_subscription` | 暂无 | 注释里说明了为什么是 PUT（同 endpoint 幂等置入），合理。 |
| 57 | POST | `/push/subscriptions/delete` | `drop_subscription` | 暂无 | 注释说明了为什么用带 body 的 POST；按 `(endpoint,user_id)` 删，越权面已封。 |
| 58 | POST | `/sandbox/storage-sweep` | `trigger_storage_sweep` | 可优化 | 响应缺 `message` 字段（信封不完整，`sandbox.py:31`）。 |
| 59 | GET | `/sandbox/cli/cheese` | `get_cheese_cli` | 可优化 | 每个请求同步 `Path.read_text()`（`sandbox.py:44`），阻塞事件循环；静态内容却无 ETag。 |
| 60 | POST | `/projects/{project_id}/site-session` | `site_session` | 暂无 | 同 #41，鉴权在签发之前。 |
| 61 | GET | `/space-applications` | `list_my_applications` | 可优化 | 裸信封（无 `message`）；offset/limit 与别处的游标分页不统一，无 `total`/`hasMore`。 |
| 62 | POST | `/space-applications/{space_id}/resubmit` | `resubmit_space` | 可优化 | 同上的裸信封。 |
| 63 | GET | `/topics/{topic_id}/members` | `list_topic_members` | 暂无 | 5 次批量查询组装名单，没有逐成员往返（`topic_members.py:46-70`）。 |
| 64 | POST | `/topics/{topic_id}/members` | `add_topic_member` | 可优化 | 服务层 `project_of_seat` 走 `list_all()`——全表扫 `agent_instance` 再在 Python 里线性找（`agent_instance/repositories.py:49`）。 |
| 65 | PUT | `/topics/{topic_id}/members/{handle}` | `update_topic_member_role` | 暂无 | 服务层 `_require_manager` + 最后一个 owner 保护齐全。 |
| 66 | DELETE | `/topics/{topic_id}/members/{handle}` | `remove_topic_member` | 暂无 | 同上。 |
| 67 | GET | `/projects/{project_id}/files` | `list_files` | 可优化 | 整棵树一次返回，无分页无上限；live 源是一次设备 RPC。 |
| 68 | GET | `/projects/{project_id}/file` | `read_file` | 暂无 | 单文件、有 `MAX_TEXT_BYTES` 上限、有 `version` 做乐观锁。 |
| 69 | GET | `/projects/{project_id}/file/raw` | `read_file_raw` | 可优化 | `source=committed` 的内容是内容寻址、不可变的，却一律 `Cache-Control: no-store`。 |
| 70 | PUT | `/projects/{project_id}/file` | `write_file` | 可优化 | `body: dict` 无类型，`path`/`content`/`version` 手工取值手写校验（`workspace.py:183-189`）。 |
| 71 | GET | `/projects/{project_id}/git/log` | `git_log` | 可优化 | 上游写死 `per_page=50&limit=50`（`forge_files.py:283`），路由不收分页参数也不告诉调用方被截断。 |
| 72 | GET | `/projects/{project_id}/git/diff` | `git_diff` | 可优化 | 整个 diff 一次回，无大小上限、无分页、无 ETag。 |
| 73 | GET | `/projects/{project_id}/topics/{topic_id}/work-summary` | `topic_work_summary` | 可优化 | 每条未结束的活都打一轮远端 forge（`comparison()` → `_head` + `default_branch` + `/compare`），只靠 15s 超时兜底。 |

## 详细分析

逐组的完整报告在同目录 `2026-09-28-api-endpoint-review/` 下，一组一份，每条都有结论表和可粘贴的改法代码：`users.md`、`topics.md`、`spaces.md`、`projects.md`、`tasks.md`、`qa.md`、`accept_feedback.md`、`teams.md`、`integrations.md`、`admin.md`、`content.md`、`realtime.md`，另附 `00-reference.md`（借鉴来源）与各组的接口清单 `*.list.md`。下面按危害与收益挑出最该先动的。

### 一、安全：三处在漏东西

1. **`POST /fetch` 是完整的 SSRF 读原语**（`app/domain/fetch/layers.py:90-95`、`rung_plain_http` `:133-138`）。对调用方给的 URL 直接 `httpx.get(...)`，`follow_redirects=True`，`domain/fetch/` 全目录搜 `ssrf|is_private|ipaddress|localhost|127\.` 零命中；正文原样回给调用方。路由注释写的是「任何已认证调用方可以读一个**公开**页面」（`routes/fetch.py:42-45`），但没有任何东西在保证「公开」。任何登录用户、以及沙箱里被提示注入的 agent，都能让后端去读 `http://backend:8081/` 或云元数据 `169.254.169.254`。改法见 `realtime.md` 第一节，核心是请求前把 URL 解析到 IP、拒绝私网/链路本地/回环，且重定向每一跳都要重判。

2. **`GET /topics/{topic_id}/accept-card` 与 `GET /topics/{topic_id}/pr-checks` 不带 `?task=` 时一次鉴权都不做**（`accept.py:260-263`、`:285-286`）。鉴权依赖只挂在 `if task is not None:` 那一支，默认分支是「取全部卡 → 渲染 → 返回」，而前端正是不带 `task` 的调用方（`frontend/src/api.ts:2128`）。泄漏 `change_body`、`gate_output`、`pr_url`、`deliverable.url`。现有测试 `test_accept_card_room_only.py:87` 用不带 Authorization 的 TestClient 断言 200，把这个行为钉成了契约。改法见 `accept_feedback.md` 第 1 条。

3. **`GET /recruitment` 把 `visibility=stealth` 团队的招募贴漏给匿名调用方**（`recruitment.py:133-163` 无鉴权依赖，`recruitment_repositories.py:57-84` 的 WHERE 只有 `status == OPEN and deleted_at IS NULL`，没有可见性 join）。泄漏 `contact` 与团队摘要，与 `domain/team/models.py:35-42` 的 stealth 约定相反。顺带：游标按 `id` 而排序按 `created_at desc`，页界不唯一；过期贴仍在列。

### 二、性能：四处随数据量线性变慢

1. **`GET /discussions` 一页 20 条 ≈ 340 条 SQL**（三重 N+1）。`_build_discussion_dtos`（`domain/discussion/services.py:217-242`）逐行算 reaction 摘要（每行 3~5 条，含 `ensure_defaults` 的 COUNT）、逐行递归 `list_discussions(page_size=2)` 取子帖样例（每行约 15 条）、逐行 `count_children`，再叠 `repositories.py:125-128` 的逐行 mention 查询。批量化后一页固定 6 条，与行数无关，响应形状不变。
2. **`GET /tasks` 与 `get_task`**（`tasks.py:2078`、`_enrich_task_models` `:551`）同请求三组查询各跑两遍；为一道题拉整板报名表，`accessDomainGroupIds` 每题两条查询（`:663-672`）。`GET /tasks` 还为 `distinctParticipants` 再跑一遍同查询。
3. **`GET /spaces/{space_id}/dashboard` 约 11N+1 条查询**（`dashboard.py:74`）。门里每项目最多 6 条权限查询，板上每项目 5 条；其中 `milestones.list_calendar` 第一句是 `mark_overdue(project_id)` 一条 **UPDATE**（`milestone/repositories.py:16/71`）——只读请求里写库；`list_ids_for_space_tasks` 在 `dashboard.py:66` 与 `services.py:511` 查了两遍。
4. **`GET /notifications` 逐行解析实体**（`notifications_flat.py:184` → `services.py:202`）。`build_notification_dto` 固定以单元素列表调 `resolve_entities_from_metadata`（`:215`），分组永远只有一个 id，每页上限 100 行 × 最多 3 个 resolver ≈ 300 次查询，外加每页一次全表 `COUNT(*)`。这是结构性的：单行进单行出，批量进不来。

同一形状还有 `GET /teams` 与 `/teams/my-teams`（每团队 3 条 SQL，`teams.py:411-421`、`:454-465`，`my-teams` 连 limit 都没有）、`GET /projects/{id}/integrations` 的 `granted()` 全表 `select(Integration)` 后在 Python 里筛、`POST /projects/{id}/alerts` 逐收件人 `user_by_handle` + 逐行 `flush()`/`refresh()`（30 人约 90 条语句）、answers 列表每答案 6 次查询（≈124 次往返/页）。

### 三、正确性：会 500 或静默出错

1. **请求侧分页参数名前后端对不上，翻页一直没生效**。`frontend/src/network/api/*` 发 `pageStart`/`pageSize`，但 34 处后端只认 `page_start`/`page_size`（answers、questions、tags、groups、materialbundles、comments、feedback、admin_feedback），这些接口的翻页参数被静默忽略、恒返回默认页。例：`network/api/tags/index.ts:13` 发 `pageSize`，`tags.py:38-39` 认 `alias="page_size"`。**FastAPI 在这个版本一个参数只能绑一个名字**：`Query(validation_alias=AliasChoices(...))` 被静默忽略，`Query(alias=AliasChoices(...))` 直接 `TypeError: unhashable type: 'AliasChoices'`（fastapi 0.137.0 / pydantic 2.13.4 实测），所以「两种拼法都收」要么改签名收两个参数再合并，要么定一个规范拼法改另一头。同族的还有 `/knowledge` 只认 `sortBy`/`sortOrder`，而前端与 `api_catalog.json` 契约快照发的是 `sort_by`/`sort_order`——**排序从来没生效过**；`GET /discussions` 声明 `parentId`，前端类型是 `parent_id`。
2. **校验缺口直接变 500**：bounty 裸 `int()`（`questions.py:500`）、`type` 原样写进 Integer 列（`:417`）、`mentionedUserIds` 非整型（`discussion/services.py:56-58`）、`commentableType` 超出 PG 枚举（只允许 ANSWER/COMMENT/QUESTION）、`name` 无长度校验而列是 `String(255)`、upvote 的 SELECT-then-INSERT 撞唯一约束。
3. **18 条写路由在响应前没有显式 commit**——本仓自己的注释记着踩过这个坑（`tasks.py:1334-1339`、`:2501-2505`）。
4. **报名/提交/评审三处「先查后插」，四张表没有唯一约束或索引**（`domain/task/models.py`）。重复行会让 `get_by_task_and_member` 的 `scalar_one_or_none` 从此抛 500，还会重复发奖。
5. **`GET /ai/quota` 读路径会 INSERT**，而 `user_ai_quota.user_id` 没有唯一约束：并发首次访问插两行，此后 `scalar_one_or_none()` 永久 500。这条要一条清重复行的数据迁移，是全部建议里唯一动存量数据的。
6. **`GET /tasks/{taskId}/ai-advice/conversations/stream` 当前永不可达**（`tasks.py:3791`）：被先注册的 `…/conversations/{conversationId}`（`:3666`）吃掉（Starlette 先注册先匹配），既有契约守卫先把 `{x}` 归一成 `{}` 所以抓不住；且前端按 `[PARTIAL]/[RESPONSE]/[DONE]` 解析而后端发 JSON 帧，端到端不可用。
7. **`POST /me/mail-drafts/{draft_id}/send` 可重复发信**（`integrations/service.py:432`）：一次普通 `session.get` 状态读，并发两个请求都读到 `"drafted"`，各发一封；单请求还串行建连 3 次（各 30s 超时，最坏 90s+，正好撞上客户端重试窗口）。
8. **12 个 `/integrations/{id}/...` 与 `/me/integrations/...` 在上游调用期间把池连接压在 idle-in-transaction**：`_usable()` 的 SELECT 打开事务后，`asyncio.to_thread` 去做 20–30s 的 IMAP/飞书往返（`mail.py:26` TIMEOUT=30、`feishu.py:94` timeout=20），`finally: await db.commit()` 才放连接。池 20+15、`pool_timeout=30s`，35 个并发就能抽干整站——正是 09-18/09-19 事故的形状。

### 四、缺索引（一次性补，收益最大）

`team_user_relation` 与 `team_membership_application` 建表时只有 PK/FK（`alembic/versions/a95752502bb0_initial_schema.py:897-934`），Postgres 不为 FK 自动建索引，于是每条 `is_team_member` / `get_team_roles` / 权限检查都在顺序扫；`blocks` 上没有以 `created_at` 打头的索引（`block/models.py:166-210` 四条索引打头列都是 topic_id/task_id），`GET /admin/memory/reads` 不带 `project_id` 就是全表扫；`question_query_log` / `question_search_log` 甚至没有生产写入方（`repositories.py:242/323` 只有定义），trending / search-terms / stats / view_count 四个读接口恒空。

## 跨接口建议

1. **请求侧分页参数定一条规矩**：拼法定死一个（建议跟响应侧的 `pageStart`/`pageSize`，前端 `network/api/*` 已经在发它），另一头跟着改；上下界定死 `pageStart >= 0`、`1 <= pageSize <= 200`。本次只落了上下界（见下），拼法等拍板。
2. **批量 DTO 当公共依赖**。N+1 的根因是「一行一个 DTO、DTO 里各查各的」。抽 `counts_by_*` / `*_by_ids` 这类批量原语，配 `counting_sql()` 把每页查询条数钉进测试，否则回归了也不知道。
3. **创建类 POST 支持 `Idempotency-Key`**（Stripe 模式：24 小时内重放首次结果）。仓库里 `app/domain/idempotency/` 是现成设施，`milestones.py:60-88` 是标准写法；`POST /projects/{id}/alerts`、`POST /me/mail-drafts/{id}/send`、`POST /teams` 都该接。
4. **缺索引一次性补**（见上第四节），另给报名/提交/评审三张表补部分唯一索引，把「先查后插」变成数据库兜底。
5. **错误体加 `documentation_url` 与 `X-Request-Id`**（GitHub 模式）。信封 `{"code","message","data"}` 与 `error{name,message,data,retryable}` 是项目既定约定、不动，加字段是向后兼容的。
6. **轮询类读接口上 ETag/304**。`app/api/conditional.py` 的 `etag_for_json` / `if_none_match_hits` 已经写好，目前只有 avatars 与 admin roster 在用；验收卡、通知、设备状态这些被轮询的都该上（GitHub 的 304 不计入限流）。
7. **限流响应带 `X-RateLimit-*` 与 `retry-after`**，让调用方能自己退避，而不是只看见一个 403。
8. **`204` 空体与信封二选一定死**。`DELETE /ai/conversations/{id}` 等返回 204（GitHub 惯例），其余走信封——同一仓库两种，客户端要分情况处理。

## 参照

1. GitHub 分页：`link` 头 + `rel="next"`，`per_page` 上限静默夹取 — https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api
2. GitHub 错误体：`{"message", "documentation_url", "status"}`（2026-09-28 对 api.github.com 实测）— https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api
3. GitHub 限流与条件请求：`X-RateLimit-*`、`retry-after`、`etag`/`if-none-match`/304 — https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
4. Stripe 幂等：`Idempotency-Key`，24 小时内重放首次结果 — https://docs.stripe.com/api/idempotent_requests
5. skills.sh（vercel-labs/skills）：skill 的清单式组织与公开 API — https://skills.sh

## 本次落地的改动与测试

这一轮落了两处改动：正常请求的返回一个字节都不变，变的只有**越界参数原来被安静吞掉、现在被拒**这一条错误路径——而那正是要修的东西。其余写在上面等拍板。

### 1. 分页参数补上下界（17 个路由文件，41 处）

原来 `pageStart` / `page_start` / `pageSize` / `page_size` 全都不设边界，负数会一路传进 SQL 的 `OFFSET`、`pageSize=0` 或上千的 `pageSize` 会把整表拉进内存。现在 `pageStart`/`page_start` 是 `ge=0`，`pageSize`/`page_size` 是 `ge=1, le=200`。

上限取 200 而不是 GitHub 的 100，是因为本平台自己就在要 200：`GET /spaces`（`test_space_membership.py:78`）和 `GET /spaces/{id}/submissions`（前端 `TaskInsights.vue:53`）都发 `pageSize=200`，取 100 会把它们卡成 400。上限逐条校过，现有的 `limit: ... le=100` 一处没动。

越界在参数校验就被拒，返回 **400 `BadRequestError`**（`validation_exception_handler` 把 FastAPI 的参数校验统一映射成 400，不是 422）。

测试：`backend/tests/integration/test_page_bounds.py`（6 条：负 `pageStart`、零 `pageSize`、超上限 `pageSize` 各拒；上限本身、常规页、不传参数各照常 200）。

另外四条既有测试照着新行为改了，两条只换写法、两条换的是断言：

- `test_page_camel_case_keys.py::test_members_empty_page_uses_camel_case`、`test_questions.py::test_search_questions_not_found` 原来拿负数分页当「空页」的写法，改成走 `page_start` 越界 / 不传，断言意图不变。
- `test_groups.py::test_get_group_members_negative_page_size` 原来钉着 `page_size=-1` 返回 200 空页（响应里 `pageSize: 0`），现在钉 400——一个服务不了的页被静默换成空页，正是这条要修的。
- `test_tags.py::test_search_invalid_page_start` 原来钉 `page_start=-1` 返回 **404**：`tags.py` 里手写了一句 `raise NotFoundError("Invalid page_start")`，把坏参数报成「找不到」。现在钉 400，并把那句手写检查删掉（参数边界已经接住它，留着就成了两套判据）。

### 2. `POST /users/verify/email` 的账号枚举加客户端预算

原来这个接口先查库、查到已注册就直接 409，**这一次 409 连 Redis 都不碰**——等于一个免费的枚举探针，能按连接速度随便问「这个邮箱注册过吗」。现在探到已注册地址要花掉一格 `ClientFailureBudget("email_code")`（与猜验箱码共用同一档，100 次/15 分钟，理由与 `request_sign_in_code` 一致：同一个来源问这两个问题是同一个攻击者），真的把码发出去才 `refund`。校园 NAT 后面正常注册不受影响（成功不占额度），喷洒枚举被限到每 15 分钟 100 次。

**这只堵住低成本喷洒，两边的回答仍然不同。**彻底的修法是让已注册和未注册返回同一个答复、把「邮箱已注册」推迟到 `POST /users`（跟 `request_sign_in_code` 的 `_SIGN_IN_CODE_REQUESTED` 一个模式），代价是「邮箱已被注册」这件事要到下一步才看得见，会改用户看到的东西——要不要做请拍板。

测试：`backend/tests/integration/test_auth_attempt_limits.py::TestClientAddressLimits` 新增两条（探已注册地址按来源限流、跨收件人共用额度，且另一个来源不受牵连；真正发码的注册流程连发多次不占额度），连同原有的 `test_wrong_email_codes_from_one_address_are_limited`、`test_verification_mail_from_one_address_is_limited_across_recipients` 一起跑绿。

### 3. 验证做到了哪一层

- 五个受影响的测试文件本地合跑：**92 passed**。`ruff check` / `ruff format --check` 干净，`pyright` 对改动到的 17 个路由文件 0 error。
- **CI 全绿**（`4cec5b8c`，run 36475419457）：backend 的 integration 0-4/4、pure、contract、search、extension、lint、migration-heads 全部 pass，另有 e2e、guards、remote acceptance / private-chat、docs build。这就是 9387 个用例的全量，在 CI 的分片上跑完的。
- 本地全量跑不完，是**机器的问题不是用例的问题**：这台工作机 4 GiB 内存、512 MiB swap（swap 已用满），`pytest -n 4 --reruns 2` 会在跑到 32% 和 39% 时把 xdist 的 worker 打死，日志里是 `[gw0] node down: Not properly terminated`，worker 一死它正在跑的那批就集体报 E/F——那条密集红区是这么来的，不是测试断言失败。要在这台机器上跑全量，得降到 `-n 1` 或 `-n 2`。

