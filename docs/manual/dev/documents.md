---
title: 房间文件与 Office
kind: 参考
summary: 房间文件的草稿历史与冲突、文档模板、Word/PPT/Excel 的渲染、编辑与格式转换。
covers:
  - backend/app/api/routes/room_files.py
  - backend/app/domain/documents/
  - backend/app/domain/project/room_files.py
  - backend/app/domain/preview/office.py
  - deploy/office-render/
  - deploy/browser-render/
  - backend/sandbox/skills/documents/
  - backend/sandbox/cheese
---

# 房间文件与 Office {#documents}

房间里的一份 `.docx` / `.xlsx` / `.pptx` 是同一份字节被四件事围着：谁改过（草稿历史）、谁能改（在线编辑器）、从哪开始（模板）、怎么被看见（渲染），以及读不出来的老格式怎么进来。

> 讲：房间文件的版本与冲突、OnlyOffice 那条链路、模板目录、渲染与格式转换服务。不讲：资料库与产物清单（见[资料库与产物](/dev/library)），预览面板（见[话题预览](/dev/preview)）和项目网站（见[项目网站](/dev/sites)），文件在任务分支上怎么进 git（见[任务 → 分支 → PR → 验收合并](/dev/delivery)）。

## 每次写都留一版 {#revisions}

房间文件存在对象存储里，不在工作目录里；工作目录是任务分支的检出，两者不是同一份东西。所有写入都走一个函数：`room_files.save_room_file`（`domain/project/room_files.py`）。它写内容，同时记一行 `RoomFileRevision`（`seq`、`sha256`、`size`、`author_handle`、`author_kind`、`source`、`note`、`editor_key`），内容按 sha 去重存放。

`source` 是闭集 `SOURCES`，不是自由文本：

| source | 谁写的 |
|---|---|
| `baseline` | 历史开始之前就存在的那一版，第一次改动前补记 |
| `upload` | 上传或从资料库复制进来 |
| `template` | 从标准模板新建 |
| `ai` | 芝士写入 |
| `editor` | 在线编辑器回调，或修订处理写回 |
| `restore` | 「恢复到此版本」本身 |
| `scheduled` | 定时任务 |

写之前取一把 `pg_advisory_xact_lock(hashtext('room-file:<room>:<path>'))`——版本检查和 `seq` 只有在没人插在中间时才是真的。冲突规则只有一条：调用方传了 `base_version`（它读过的那一版），而当前版本已经不同，就抛 `ConflictError`，**绝不覆盖**。新建文件、以及明知要顶掉当前版的恢复，传 `None`。文件内容与上一版相同时直接返回上一行，不造空版本。

「恢复到此版本」（`restore_revision`）不是回退历史：它把旧内容当成新的一次保存写进去，所以被顶掉的那一版也还在一键之外。历史只能到房间文件为止——**交出去过的那一版在验收卡自己的快照里，从这里够不着**。

## 在线编辑器 {#editor}

`documents/editor.py` 对着 OnlyOffice Document Server。选它而不是一个富文本编辑器，理由写在模块开头：编辑器直接改 Office 文件本身，没有内部格式需要导入导出，人保存的就是芝士下一次读到的；而一份报告粘进 HTML 编辑器再写成 Word，样式、编号和图表是**无声地**丢掉的。

三方、两种令牌，边界靠令牌划：

- 浏览器向后端要配置（`editor_config`）。配置用与编辑器共享的密钥签名（`settings.office_editor_jwt_secret`），编辑器据此决定要不要打开。
- 编辑器来取文件、以及之后回存，走的是后端为**这一份文件、这一版、这一个人**签的链接令牌（`sign_link` / `read_link`，`_LINK_TTL` 12 小时）。这两个端点（`/office-editor/files/{link}`、`/office-editor/callback/{link}`）不认别的凭据，因为编辑器手里没有我们的别的东西。
- 回存回调由编辑器用共享密钥签名（`verify_callback`），签名不对或没有签名直接 403，所以没有人能靠猜 URL 往房间里灌字节。

`document_key` 是「一份文件的一版一个 key」：同一个 key 的人在一起编辑，新 key 就是一次新加载——这正是新版本该有的行为。可编辑的是三种 OOXML（`_KINDS`：docx/xlsx/pptx）；`.doc` 这类只读打开（`_VIEW_ONLY`），因为存回来会换成另一个格式顶着旧名字。资料库里的原件不给编辑，返回 `copyable: true`，让用户先在房间里复制一份。

回调只认两个状态：`_MUST_SAVE = (2, 6)` 才去下载并保存，`_SAVE_FAILED = (3, 7)` 记一条错误日志。任何非 `{"error": 0}` 的答复都会让编辑器告诉人「没保存成功」，所以那句话只在真的时候说。回调给的下载地址只取路径（`internal_download_url` 从 `/cache/files/` 起截），主机永远是部署内的编辑器——否则一个回调就能把后端指向任意地址。

两条容易被当成 bug 的规则写在这里：

- `saved_by_session`：编辑器在最后一个人关闭时会再交一次内容，即使每次改动都已经「保存」过。如果这期间芝士又存过，这次重复看起来就像一次冲突保存——它不是，是已经留着的一份的副本，所以直接回 `{"error": 0}`。
- 真冲突时（别人在编辑器打开之后存过），两份都留：对方的是文件，这一份落在旁边，名字带「（<谁> 的修改）」。丢掉任何一份都是有人白改了。

## 模板与复制 {#templates}

`documents/catalogue.py` 里六份真 Office 文件（`templates/` 目录，由 `scripts/build_document_templates.py` 重新生成）：`report`/`proposal`/`weekly` 是 docx，`project-deck`/`research-deck` 是 pptx，`analysis` 是 xlsx。给的是结构和样式——标题、带样式的表格、已经接上数据的图表——内容位置留 `【占位】`。

从模板新建（`POST /topics/{id}/files/new`）有一条硬规则：**存的名字必须保住模板的格式**（`analysis` 只能存 `.xlsx`），否则就是一个没有任何程序会按名字打开的文件。`CLI cheese template new` 走这条路，建完立刻把文件取回本机，再用 `documents` 技能的 `template.py fill` 填占位。

`POST /topics/{id}/files/copy` 是另一条道：把资料库里那份原件复制成房间里一份可编辑的。原件永远不被改写——它是用户给进来的，房间在自己那份上干活；这也是「拿用户自己的文件当模板」不覆盖他的办法。

## 渲染：看不见就等于没交 {#render}

一份 Word 报告、一个 deck 或一张预算表本身就是交付物，而一件没人能看就下载的交付物离「没交」只差一点。浏览器天生画 PDF，所以在这里（`deploy/office-render`）转一次 PDF，就是把这些格式放上屏幕的办法。

它是一个独立服务而不是后端里的一个库，原因是 LibreOffice 装上差不多 800MB，而且要一个可写的 profile 目录；沙箱镜像装不下（那是别人的基础镜像，房间也没有 root），后端镜像也不该装。它答两个问题，都是靠把文档交给 LibreOffice：长什么样（PDF）和公式算出来是多少（重算后的工作簿）。**对调用方只读**：收字节、还字节，什么都不留，每个请求在自己的目录里干活，响应发出前删掉。

同一个服务也做 `/convert` 和 `/recalc`。这里必须记下的两个测量结论：

- LibreOffice 一个 profile 只许一个进程，而它执行这条的方式是问题本身——共享 profile 的第二个进程会**安静地退出、什么都没写**。实测：五个并发共享一个 profile 产出两份 PDF 且没有报错；五个各自一个 profile 在 1.18 秒里产出五份。所以每个请求一份 profile，信号量只是防一波把机器撑爆。
- LibreOffice 默认信公式旁缓存的值并原样写回（`OOXMLRecalcMode` 是 1）。实测：`SUM(A1:A2)` 盖着 2 和 3、旁标缓存 999，转出来还是 999；同一个格完全没有缓存值时出来才是 5。命令行没有开关，设置只能通过它加载的 profile 进去，所以重算请求先往 profile 里种 `RECALC_PROFILE`（把模式改成 0）。重算失败因此正是那种没有症状的失败：文件打得开、公式栏是对的、底下的数还是改之前的。

前端那侧的一层薄壳在 `domain/preview/office.py`：它按**内容哈希**缓存换来的 PDF（内存有界、磁盘也有一份，因为内存缓存每次发布都没了），`prewarm` 在保存后异步先把 PDF 转好，好让打开是个命中。表格故意不走 PDF——一张表转成 PDF 就丢了它是表的理由：列断页、格子不再有地址——所以浏览器直接按原始字节画它。另一个服务 `deploy/browser-render` 是给网页用的（一个共享浏览器，只导航、只回 markdown/HTML，暴露不了点击或提交），不属于文档链路，放这里只作对照。

## 修订与两版差在哪 {#tracked-changes}

`.docx` 里的修订（tracked changes）平台这一半是 `documents/revisions.py`。它加载 `backend/sandbox/skills/documents/scripts/office.py`（`SourceFileLoader`，临时关掉字节码写入）——判断只有一处，两处实现必然漂。`revisions_in` 列出每一处待决定：insertion / deletion / 别的，`decide` 按行号接受或拒绝，然后重打包。接受一处插入就是去掉包装留下文字，接受一处删除就跟着删掉文字，拒绝反之；整个过程是 XML 上的确定变换，所以读完之后人下载到的就是 Word 会产出的那份文件。`decide` 拒绝清单上不存在的行号（「清单可能已经变了，重新读一次」）。

`documents/compare.py` 是差异那一半：按字节比一份 `.docx` 只答得出「不一样」，因为它是 zip。所以三种 Office 文件各按读者的单位比：Word 按段落（同样用那个脚本读段落正文，段落里的字词级增删；字一样而段落或字符格式变了的段落单独列出，「只改了格式」说得出改在哪），表格按单元格（之前和之后的值，以及变的是公式还是数值），幻灯片按页（按阅读顺序对齐，标出新增、删除和改过的页）。「改动」里的 Office 文件（`review/document_compare.py`，第一次交付和任务开始时的版本比，被退回过的和退回时那一版比）和产物页的版本比较用的是同一个结果。读不出来（加密、坏掉、根本不是 Office 包）返回 `None`，退回按字节比，而不是让一次版本比较变成一条错误。

## 换格式：convert 与 recalc {#convert}

`documents/convert.py` 是 `deploy/office-render` 的薄的一半，两件事共用一个机制：

- **老格式升级**。`.doc` / `.ppt` / `.xls` 不是 zip，房间里什么都读不了也写不了（`LEGACY_SUFFIXES` 单独列出来，因为给用户的说法不同：对这些平台不是在提供便利，是唯一的入口）。转换结果是新文件，**原件永不覆盖**（`upgraded_name`：`报告.doc` → `报告.docx`，摆在旁边），而且 `cheese convert` 会明说这是一次格式升级、让他决定往后发哪一份。
- **自己看一眼版面**。排版错了是无声失败（文字提取照样正确、文件照样打开），所以转成 PDF 再在机器上栅格化成图片来看。

能转什么是写死的表 `CONVERTIBLE`（`.doc → docx/pdf`、`.xls → xlsx`、`.docx → pdf` …），而后端镜像里也存一份同样的表，好让一个不可能的请求在这里就被一句人话拒掉，而不是在下一跳收到一个 HTTP 码。目标格式等于源格式的项**故意缺席**——那是重算，它需要比换个 filter 更多的东西。转换结果会验头（`%PDF` 或 `PK`），挡住把一张错误页写成真名字的文件。

`documents/spreadsheet.py` 是重算的薄的一半。它把结果读回来，顺手报出**算不出来的格**：Excel 存在格里的七个错误值（`#DIV/0!`、`#REF!` 等）只是带 `t="e"` 的普通文本，下游不认清这张单子就不会当它们错。它们按人指的那个样子报出来——`Sheet1!B7`，和预览面板用的是同一个坐标。只收 `.xlsx`：不 `.xlsm` 是因为重算走的是普通 xlsx filter，宏工作簿会顶着自己的名字回来、宏没了，而没有任何地方会说这件事。

## 边界与坑 {#traps}

- **房间文件不在工作目录里，产物不进 git**。房间文件是用户传进来让芝士改的，改完在房间里拿走事情就结束了。要留下是「保存到资料库」这一个动作（`room_files.save_to_library`，撞名不覆盖、跟着资料库自己的规矩走），**它不上产物清单、也不进 git**。真正的「文件本身就是源」走正常交付：任务分支上改、递卡、人采纳合并，二进制从那个口进 git。
- **「恢复到此版本」够不到交出去的那一版**。交付版本的快照在验收卡里，不在房间文件的修订链上。
- **编辑器打开的那一版是写死在链接令牌里的**。回调用 `base_version=target.version` 去保存，所以打开之后有人改过，两边都留，不互相盖。
- **`prewarm` 是发射后不管**。保存不等 LibreOffice；转失败就在有人打开预览时，带着它那句话再失败一次。
- **格式转换服务整个是只读的**，它不留任何东西，也从不判断内容——所以「转换」这件事在平台这一侧没有任何缓存可清。
- **`office-render` 的 404/405 是部署状态，不是文件的毛病**。跑着的渲染器比这个端点老（两个镜像来自不同的 tag）会这样回答；这类被当成 `Unavailable`（`SystemBusyError`），不要往文件上找原因。
