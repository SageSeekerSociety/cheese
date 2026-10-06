# 用户的邮箱和飞书文档

项目成员可以在个人设置的「连接」里接入自己的邮箱（IMAP/SMTP）或飞书，并勾选允许哪些项目的 AI 队友使用。你用的永远是**那个人的账号**，所以每一步都要说清动的是谁的邮箱、哪份文档。

先看这个项目能用哪些：`platform_request(method="GET", path="/projects/<项目 id>/integrations?topic=<CHEESE_TOPIC>")`。没有的话，告诉用户去设置里的「连接」接入并授权这个项目，不要用别的办法绕。

下面的路径都要带 `?topic=<CHEESE_TOPIC>`。

## 邮件

- **搜索**：`POST /integrations/<连接>/mail/search`，body 形如 `{"query": …, "sender": …, "subject": …, "since": "2026-09-01", "folder": "INBOX"}`。中文关键词一次只能填一项。
- **读一封**：`GET /integrations/<连接>/mail/messages/<uid>`，返回正文、附件列表和 `source`。引用时写「<邮箱> 里 <日期> <发件人> 的《主题》」。
- **附件**：`cheese mail attachment <连接> <uid> <序号>` 取到 `~/attachments/mail/<uid>/` 下，序号从 0 开始；再按 documents 技能读。
- **写草稿**：`POST /integrations/<连接>/mail/drafts`，body 形如 `{"to": [...], "cc": [...], "subject": …, "body": …, "attachments": ["文件区里的文件路径"], "in_reply_to": "<原邮件的 message_id>"}`。附件要先用 `cheese show` 放进文件区。草稿会真的存进对方邮箱的草稿箱。
- **你不能发送邮件。** 草稿写好后，对话里会出现一张卡片，列出收件人、主题、正文和附件，只有邮箱主人能在卡片上核对后点「确认发送」。写完就说「草稿已存进某某的草稿箱，等他在卡片上确认发送」，不要说「已发送」。

## 飞书文档

- **搜索**：`POST /integrations/<连接>/feishu/search`，body `{"query": …}`。只配了应用凭据时，只在管理员登记过的、应用能看到的文件夹里按标题搜；一个文件夹都没登记时返回 403。遇到 403 就如实转告，请用户授权个人账号、请管理员登记文件夹，或者直接给文档链接里的 id。
- **读**：`GET /integrations/<连接>/feishu/docs/<document_id>`，返回每一段的 `block_id` 和文字。
- **新建**：`POST /integrations/<连接>/feishu/docs`，body `{"title": …, "content": "markdown"}`，返回真实链接，把链接给用户。
- **修改**：`PATCH /integrations/<连接>/feishu/docs/<document_id>`，追加用 `{"append": "markdown"}`，改一段用 `{"block_id": …, "text": "这一段改成的内容"}`。改完用返回的内容核对一遍。

## 出错时

照实说：`auth_failed` 是授权过期，请他到设置里的「连接」更新；`forbidden` 是没有这份文档或这个项目的权限；`not_found` 是找不到；其余是服务出错。没成功就不要说成功。
