---
title: 资料库与产物
kind: 参考
summary: 不在 git 树上的那一半文件：资料库、房间文件、交付快照、产物清单和课件资料集。
covers:
  - backend/app/domain/library/
  - frontend/src/lib/libraryFolders.ts
  - backend/app/domain/materials/
  - backend/app/domain/project/artifacts.py
  - backend/app/domain/project/room_files.py
  - backend/app/api/routes/projects.py
  - backend/app/api/routes/library.py
  - backend/app/api/routes/topics.py
  - backend/sandbox/cheese
---

# 资料库与产物 {#library}

不在 git 树上的那一半文件：用户给项目的资料、房间里贴出来的东西、交出去的那一版、清单上每一项的名字，以及课程用的课件。

> 讲：这些文件各住在哪个根下、按什么寻址、怎么写进去、产物清单的一项怎么生怎么长。不讲：房间文件的历史与冲突（见[房间文件与 Office](/dev/documents)），交付链路本身（见[任务 → 分支 → PR → 验收合并](/dev/delivery)），采纳怎么落地（见[验收与采纳](/dev/accept)）。

## 两个根，两种寻址 {#roots}

`domain/library/` 是这半个世界的全部。它的字节都在 `settings.workspace_root` 下，和项目那唯一一个 git 源（`domain/repository/service.py`）互不相干——被托管的仓库只装芝士替用户做的活，用户给的资料、贴进来的截图、发布出来的预览产物都不是那个活的源，所以**既不进 git 树，也不落在检出目录里**（结论 49 / 不变量 I21b）。

| 在哪 | 属于谁 | 寻址 |
|---|---|---|
| 资料库：记录表 `library_files` + 每一行的 `location` / `blob_key` | 项目 | 按原名，名字就是身份 |
| `.room-files/<project>/<room>/` | 一个房间 | 房间内相对路径 |
| `.room-file-history/<project>/<room>/` | 一个房间 | 内容 sha256，房间路径够不着 |
| `.artifacts/<project>/<card>/` | 一次交付 | 文件名，卡分目录 |

资料库和另外三个不一样：它不是一个目录，是一张表。列出来的是表里的行，字节在每一行自己说的地方（`blobs.py`，今天只有本地磁盘一种，新写的字节在 `.library-blobs/<project>/<记录 id>`）。名字和字节的位置分开，所以改名、挪进文件夹只改名字，以后把字节放进对象存储也只是多一种 `location`（#2114）。

房间文件这几个根的 `_safe_path` 只有包含关系这一条检查——和仓库那侧的同名检查不是一条规则，那边还要挡 `.git`，因为那边的根是一棵 git 树，这两个根里没有 git。房间文件里 `library/` 这个前缀被留着：`write_room_file` 见到它以「`library/` 留给资料库」拒掉，否则读的人会拿到房间那份、以为看的是资料库里的原件。

## 资料库：名字就是身份 {#library-names}

`records.add` 保留用户给的名字，撞名不覆盖：拿下一个 `(n)`（`service.next_name`，编号加在最后一层上）。**分配名字就是插入那一行**：`(project_id, name) WHERE superseded_at IS NULL` 上的唯一索引让两个同时在飞的同名上传里后到的那一个去拿下一个号，「先查再写」会丢掉一份。返回的是最终落下的名字，调用方别自己拼。

一份资料在消息里、在字节端点上都是同一个地址：`library/<名字>`（`library_ref` / `library_name`）。**不拷贝**——一份资料在这个项目里只有一份字节。送上机器的那一份是另一回事，它落在会话 home 的 `attachments/` 下（`domain/agent/place.py`），**不落在检出目录里**，芝士拿到的是机器报回来的绝对路径；`cheese library get` 走的也是这条（默认落到 `$HOME/attachments/library/<名字>`）。

上传这条路上有一个岔口（`POST /topics/{id}/attachments`）：用户挑出来或拖进来的文件进资料库；剪贴板里贴进来的那张图**不进**（`origin="clipboard"`），落在房间文件区一个 `uploads/<随机串>/` 里。理由是资料库的前提是「名字就是身份」，而截图没有名字，`image.png` 是浏览器替它编的，它只属于那条消息。已经是资料库里那一份被当附件再选一次时，一个字节都不写，直接回它自己的地址。

「扔掉一份资料」（`records.remove`）不找替代品：旧消息里那枚 chip 随之打不开，这是对的——那条引用指的就是这一份，在它的位置上摆一份别的东西，才是把读者读到的内容换掉。`records.read_attachment_text` 遇到「资料已经不在」与「地址写错了」给的是两句不同的话。

## 资料库的记录表 {#library-records}

`library_files`（`domain/library/models.py`，读写在 `records.py`）一行是一份存下来的字节：名字、谁放进来的、在哪个房间、什么时候、多大、sha256、字节在哪。读、判断在不在、列清单、项目导出都查这张表，不扫目录。三条放进来的路都经过 `records.add`：话题里上传（`POST /topics/{id}/attachments`）、从房间留下（`save_to_library`）、资料库页上直接放（`POST /projects/{id}/library`）。先记一行、再写字节，请求结束时一起提交。

「替换为新版本」（`PUT /projects/{id}/library?path=`）是人明确说「这是同一份的新版本」，只有这时候同一个名字才换字节：旧的那一行标上 `superseded_at`，它的字节原地不动，新字节记新的一行。所以同一个名字可以有几行，`superseded_at` 为空的是现在这一份。引用这个名字的旧消息从此读到新的一份——这正是替换的意思。字节端点因此不让浏览器凭缓存直接用（`Cache-Control: private, no-cache`）。删除一份资料连同它的所有行和字节一起扔掉。

`blob_key` 是迁移 `0c800ff1db2f` 加的，在那之前写下的行是空的：它们的字节还在当时的目录里（现在这一份在 `.library/<project>/<名字>`，被替换下来的在 `.library-history/<project>/<记录 id>/`），`records.blob_key` 按这两条推出来。那次迁移不回填它，因为部署时还在服务的旧版本做一次替换就会把旧字节挪进历史目录，回填的键就指错了；下一次迁移补齐后把列设为非空，推导那几行随之删掉。

列表上的房间名只给读得了那个房间的人（`readable_topic_ids`），「被几条消息引用」也只数这些房间里的。放进、替换、删除都只有人能做：一轮里铸出来的凭据在 `authorize_project` 那里只读得进来。

## 文件夹与挪动 {#library-folders}

名字里的 `/` 就是文件夹，没有文件夹表：一个文件夹在它里面还有文件时存在，前端从清单里算出每一层（`frontend/src/lib/libraryFolders.ts`）。一个名字不能既是文件又是文件夹（`records._taken`）：`报告` 是文件时 `报告/附录.md` 放不进来，反过来同名的文件拿下一个号。

`POST /projects/{id}/library/move`（`records.move`）把一份资料或一个文件夹改名、挪到别处，`to` 是新的完整名字。只改 `name`，字节不动，被替换下来的几版跟着走；新名字被占了就拒绝，不替人编号。引用旧名字的消息一起改过去：附件块的 `library/<旧名>`，和正文里的 `<&library/<旧名>>`。芝士自己会话记录里写过的旧名字改不到，它照旧名去取会得到「不在」，再列一遍资料库就找得到。上传可以带 `folder`，删除给一个文件夹就连同里面的一切一起删。

## 写回资料库：一个动作 {#save-to-library}

房间里的文件不是项目产物。用户传一份进来让芝士改，改完在房间里拿走，事情就结束了。想留下就得有人按一下：`room_files.save_to_library`（`domain/project/room_files.py`），入口是 `POST /topics/{id}/shown/save`，而且要**人**——一轮里铸出来的凭据在 `authorize_project` 那里只读得进来，所以芝士摆得出东西，却留不下它。

留下这件事**不上产物清单**（结论三：清单上的一项要「会交给项目外的人」，一份留着以后用的文件不满足它，为了让它上榜就得凭一次按钮伪造一条交付记录），**也不进 git**（结论五：成品不进库）。真正「文件本身就是源」的那条路走正常交付：芝士在任务分支上改、递卡、人采纳合并，二进制从那个口进 git。

## 交付快照与产物清单 {#artifacts}

`artifact_snapshot_path(project_id, card_id, name)` 是「这一版交出去的那一份」的位置。一版是一次交付，一次交付就是一张采纳了的卡，所以快照**按卡分目录**：同一项产物的七版互不覆盖，而撤回采纳只改卡的状态、不动字节。字节不在库里也不在表上——成品是从源构建出来的，等半年后有人要下载时再重建一次，依赖已经变了，重建出来的和当时交出去的不是一份东西。名字只取最后一段：交付物的身份是「哪一版的那一份」，它在工作目录的哪个子目录不是它的身份。`read_artifact_snapshot` 对「交付物落地之前递的那几张卡」单独说一句话，别让它读起来像文件丢了。

`domain/project/artifacts.py` 是产物清单本身（结论二、三）。一项的**生**只有一种方式：交付。没有「先登记一项」这回事——一样东西是在第一次交付它的那一下才存在的。

| 交付形态 | 清单上怎么处理 |
|---|---|
| 合并（代码仓库这类项目） | 平台自己认（`for_repository`），一个项目只有一个仓库，所以没有可判断的 |
| 一份文件 / 一个地址 | 这才有得选：沿用一项（`reuse`）或声明一项新的（`claim`） |

`reuse` 只认 id 不认名字，因为名字写错不报错：`报告` 和 `结题报告` 都是合法名字，按名字认一次手滑就是清单上多一项；id 错了要么解析不出来、要么不在这份清单上，两种都是当场报错，报错里连清单现有什么、各自的 id 一起给出来（读这句话的是一个下一轮就要重递的 agent）。`claim` 的名字已经在清单上就报错——它挡不住「《报告》其实就是《结题报告》」（那要人看），但挡得住「明明是同一项却又声明了一次新的」。`for_repository` 认的是 `delivers_repository` 这一位，不是名字：人随时会改名，按名字找的话改完名的下一次合并就再长出一行。

每项带一句 `about`：说清它是什么、给谁的。约束全部来自它唯一的用途，所以是可检验的：这句话在第 1 版和第 20 版都得成立；换到另一项头上也说得通就是白写；说「是什么、给谁」不说「做了什么」。前三条机器判不了，写在递卡工具（`cheese_accept_request`）的说明和 cheese 技能里；机器判得了的两条在 `clean_about` 里当场挡：太长（`ABOUT_MAX = 80`），以及和本次改动的标题一模一样（那正是清单长成一份改动列表的那条老路，按归一化后比，免得一个尾随空格绕过去）。名字也有对应的一条：`clean_name` 会摘掉整个名字外面那一对括号（`《结题报告》`），但只在里面不再出现同一对符号时才摘。

**长**：往后每次交付沿用同一项，一版就是第 N 张点名它的采纳了的卡。版号不在任何一张表上——按采纳时间排开数下来就是它（`versions`），所以撤回一次采纳，它后面那几版的号自己往前挪。计数器不行：漏加一次、漏减一次都不报错，只会让清单上的版号和真交出去过的东西悄悄对不上。

**在不在清单上**由卡决定，不由表里有没有行决定（`_claims` + `list_for_project`）：交付落地过 → 在，显示第几版；还没有落地但有一张在飞的卡声明了它（`_LIVE_CARD_STATUSES`：pending / pending_gate / conflict）→ 在，显示「尚未交付」，别的房间接着交付它用 `reuse`，不会重复新建；两样都没有（卡被驳回、作废了）→ **不在**，但表里那一行留着，它是这个名字的身份，同一个名字再被声明时落回同一行，那一项的历史因此是连着的。

改名、合并、删除都是人的动作，因为「这两项是不是同一个东西」要人判断。改名改的是这一行，卡指着的是行的 id，所以改完之前的交付照样算这一项的版本；重名不自动合并。合并（`merge`）就是把 `source` 名下的卡改指向 `target`，版本数随之变成两边加起来，不需要另搬什么。删除把这一项从清单上去掉，声明过它的卡留在原处、只是不再指向任何一项（外键 SET NULL）——那些交付确实发生过，改写它们等于往已经落地的历史上安一个别的声明。

`GET /projects/{id}/artifacts` **没有配套的新建入口**，这不是还没做：清单由交付长出来，所以这里没有 POST。

## 课件与资料集 {#materials}

`domain/materials/` 是另一件事，不属于项目的文件：`Material` 是上传的一份课件（`type`、`url`、`name`、`uploader_id`、`download_count`、`meta`），`MaterialBundle` 是把若干份课件编成的一套（标题、说明、评分、评论数，关系在 `materialbundles_relation`）。它**只带 uploader，没有归属团队**，`GET /materials/{id}` 对任何登录读者都开着，所以课程点名自己老师安排的课件时不泄露什么；`ensure_exist` 仍然拒绝拼错的 id，因为写它的是一个能指出哪一栏错了的表单。可见性的账不在这一层算。

## 边界与坑 {#traps}

- **资料库那一份是只读的**。房间侧对 `library/<名字>` 一律拒写（`write_room_file`、`POST /files/new`、`POST /files/copy`、修订处理都挡），要改就先复制一份进房间。
- **快照不是「按名字的最新一版」**。它按卡分目录，所以一份产物交过七版就是七个目录；想看某一版的字节只能通过那一版的卡（`GET /projects/{id}/artifacts/{artifact}/versions/{card}/file`，加 `preview_pdf=true` 顺带转成 PDF）。
- **撤回采纳不动字节**。卡的状态变了，`.artifacts/<card>/` 里的文件还在；`list_for_project` 也不再把它数进版本。
- **清单上的一项不会因为没字节就不在**。`link` 型交付只记指针（`deliverable_url`），`merge` 型干脆没有文件；`deliverable_kind` 为 NULL 的卡说的是「没声明过」，和 `merge` 是两件不同的事。
- **`_libraries` 没有 git 备份**。资料库的字节和另外三个根都在 `workspace_root` 下，跟着平台自己的存储走，不在任何项目的仓库里；它们的保护来自平台侧，不来自项目的分支。
