---
title: 本机目录授权
kind: 参考
summary: 让芝士在用户自己电脑的某个目录里干活：授权、判定、审计和撤销。
covers:
  - backend/app/domain/local_fs/
  - backend/app/api/routes/local_dirs.py
  - backend/tests/unit/test_local_fs_sharing_guard.py
---

# 本机目录授权 {#local-fs}

让芝士在用户自己电脑上的某个目录里干活：一次明确的、可撤销的、按目录给的授权，每次访问都留痕。

> 讲：授权这条线本身——什么算一个授权、路径怎么判、判定怎么写、审计与推送。不讲：设备与连接器整体（见[机器](/dev/machines)），执行通道怎么把调用落到机器上（见[执行通道](/dev/execution)）。

## 三条塑造整个包的事实 {#facts}

1. **授权点的是一个目录，从来不是一台机器、也不是一个盘。** `C:/` 和 `/` 在门口就被拒（机制在 `paths.normalize`，拒绝在 `service.grant_directory`）。
2. **平台不是执行点。** 文件在用户的机器上，所以设备自己也执行一遍——用平台推给它的那份授权集副本（`cli/internal/localfs`）。留在这边的是权威记录、平台在向设备开口**之前**做的判定，以及审计。两边谁单独都不被信任。
3. **授权不带来分享。** 一个授权是别人磁盘的访问钥匙，所以它绝不能跟着结果、附件或克隆走。这条纪律由测试守，不由注释守：`tests/unit/test_local_fs_sharing_guard.py`。

## 数据形状 {#records}

| 概念 | 关键字段 | 说明 |
| --- | --- | --- |
| `DirectoryGrant` | `path`（给人看的标准文本）+ `key`（比较用的折叠键）、`platform`、`mode`、`scope`、`owner_user_id` | 两个都存：只能比较的授权显示不出来，只能显示的授权没法安全比较 |
| `GrantMode` | `read` / `read_write`，判据是 `permits()` | 不是权限矩阵：读的那个给「只该看、不该改」的材料，可读写的给工作目录。**读授权永远满足不了一次写**——这就是它必须被存下来而不是被推断的原因 |
| `GrantScope` | `project` / `user`（`covers_project()`） | 项目范围只管这一件事（为一篇论文授权的目录，下个月的任务不会悄悄用得上）；用户范围管这个人的全部事务，直到撤销。界面上先给的是更窄的那个 |
| `AccessRecord` | `device_id` / `grant_id` / `owner_user_id` 都是裸字段，**没有外键**；没有 `updated_at` | 审计要在被审计的东西没了之后还能读，所以它不级联、也不可变。**拒绝与放行同样记**：只记成功过的日志答不了边界问题——它只显示了从没发生过的那些拒绝 |
| `Verdict` / `Decision` | `decision`、`reason`、`detail`、`grant` | 给屏幕用的判定结果，原因跟着一起走 |

仓储接口里**没有**「按路径前缀查授权」这种读法，也不存在按路径列授权的接口：包含关系在服务里、按规范化后的段来判。一个能拿数据库回答「哪个授权覆盖这个路径」的仓储，就是把字符串前缀 bug 换成带数据库的版本。

## 路径规则 {#paths}

- `normalize()` 按平台分派（`_windows` / `_posix`），`_collapse` 处理 `.` / `..`；**往根之上爬是拒绝，不是夹到根**。上限：`MAX_PATH_LENGTH = 4096`、`MAX_SEGMENTS = 256`，Windows 保留名也在拒绝之列。
- 拒绝了抛 `PathRefused(reason, detail, raw)`，`raw` 是用户原样输入的那个串——屏幕要能说清是哪一句被拒了。
- 比较只在规范化结果上做：`contains()` / `is_within()` 收的是段，不是字符串。
- 给人看的 `path` 与比较用的 `key` 一起存，是因为折叠后的键没法显示（大小写、分隔符都变过样）。
- 传进来的路径**已经在设备上解析过**（软链接跟到底、`~` 展开成家目录）：平台没有能力做这两件事，它只规范化拿到的东西，并且拒绝任何不是绝对路径的输入。

## 授权、撤销、判定 {#grants}

- `grant_directory`：项目范围必须给 `project_id`，用户范围必须不给；开放式的路径（根、盘符）当场拒，抛 `GrantRefused`。撤销是 `revoke`，写 `revoked_at` / `revoked_by`，不是删行。
- `DeviceGrants` 带一个 `_fingerprint`：推给设备的是**整份在用的授权集**，指纹让「要不要重推」有据可依。
- `authorize()` 的答案与理由：

| 判定 | `reason` | 屏幕上的话 |
| --- | --- | --- |
| 拒 | `no_grant` | 这个路径不在任何授权目录里 |
| 拒 | `read_only_grant` | 这个目录只授权了读取，不能写入 |
| 准 | `granted` | 已授权 |

- 每一次判定都先写审计行再返回（`_record`），而且**从不吞掉写失败的异常**：审计写不下去的时候，一个「已放行」的判定没有意义。
- `list_access` 分页上限 `MAX_ACCESS_PAGE = 200`。

## 推给设备与降级 {#push}

`enforcement.py`：

- `push_grants` 把整份在用的授权集推给设备，尽力而为：`PushOutcome` 带 `delivered` 与 `reason`，取值 `delivered` / `device_offline` / `device_error`。推失败**不上抛**，它在 API 的响应里以 `delivery` 出现。
- `plan_local_access()` 是纯函数，判的是「这件事现在能不能够到那个本机目录」，三种状态且顺序有意义：

| 状态 | `reason` | 行为 |
| --- | --- | --- |
| 一个授权都没有 | `no_grant` | **静默**回退到平台工作区：这不是降级，是「本来就不需要本机目录」的寻常情况，没什么要告诉人的 |
| 有授权但机器离线 | `device_offline` | 带原因回退到平台工作区：「等那台电脑开机」不是一个房间能照着跑的计划，所以要给一句人话 |
| 在线 | `device_online` | 可用 |

## 对外接口 {#api}

`api/routes/local_dirs.py`，四个端点，都挂在 `/connector` 下：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/my/devices/{device_id}/directories` | 列授权，可选 `include_revoked` |
| POST | `/my/devices/{device_id}/directories` | 建授权；路径被拒时 422，正文带那句中文原因 |
| DELETE | `/my/devices/{device_id}/directories/{grant_id}` | 撤销 |
| GET | `/my/access-log` | 审计（`list_access`） |

- `_require_owned_device` 对别人的设备回 404 而不是 403：403 会证实「这个 id 存在」，404 不会——枚举要先被堵住。
- 提交之后再推给设备，推的结果写在响应的 `delivery` 里：授权已经成立，不该因为推不上去就回滚。

## 设备那一侧 {#device}

`cli/internal/localfs/{paths.go,grants.go,fsops.go,wire.go,resolve.go,store.go}` 是**故意**的一份镜像：路径规则两边各写一次，各自在自己的位置上判。设备侧的常数：

| 常数 | 值 |
| --- | --- |
| `MaxReadBytes` / `MaxWriteBytes` | 8 MiB（更大的文件直接拒） |
| `MaxListEntries` | 2000 |
| `MaxListDepth` | 4（递归列目录的下探上限） |

- 操作种类是一个封闭集合（读、写、列目录三样），不是可扩展的插件点。
- 应答把两件事分开放：`Decision` + `Reason`（与平台同一批字符串，例如「这个路径不在任何授权目录里」）说授权判定；`Error` 留给**授权通过之后**才出现的执行错误（文件不存在、太大）。混在一起，屏幕就分不清「不让」和「没做成」。

## 边界与坑 {#traps}

- 平台侧的判定入口（`LocalDirectoryService.authorize` 与 `plan_local_access`）目前只有测试在调：**生产上的执行点是设备侧那份副本**。平台留的是权威记录、事前判定和审计三样。
- 撤销不是「把行标掉就完了」：设备手里有自己那份授权集，撤销要等推送到位才在那台机器上生效——`DeviceGrants._fingerprint` 就是为这件事存在的。
- 再授权一次同一个目录是**幂等**的：键、范围、项目、模式四样都一样时返回既有那条，不新增第二行（否则审计会重复计数）。撤销之后再授权才会新建一条——查重只看没撤销的那些。
