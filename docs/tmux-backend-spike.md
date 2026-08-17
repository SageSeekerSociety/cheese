# tmux/hooks agent backend — spike 结论 (2026-07-09)

验证:交互式 Claude Code 跑在 tmux 里,用 send-keys 驱动,hooks 吐结构化事件。
镜像 = sandbox base + tmux 3.3a + ttyd 1.7.7(静态二进制,debian 无包)。

## 证实(全部通过,中文逐字节完美)
- 交互式 `claude` 在 tmux 会话中可被 `load-buffer`+`paste-buffer`+独立 `Enter` 驱动。
- hooks 在交互模式下照常触发,事件与 AgentEvent 1:1 映射:
  - `SessionStart.session_id`      → AgentSessionInfo
  - `PreToolUse{tool_name,tool_input}` → AgentToolUse
  - `MessageDisplay.delta`         → AgentMessage(注意:每条消息**多次** flush,
    每批新完成的行一次,带 `message_id`/`index`/`final`;spike 的回复短到单次
    flush 装得下,曾被误读成"一次 hook 一条消息"。现由 `MessageAssembler`
    按 `final` 拼回整条消息——见 `hook_events.py`)
  - `PostToolUse{tool_name,duration_ms}` → 工具完成
  - `Stop{last_assistant_message,transcript_path}` → AgentResult
- ttyd `-R` 只读镜像:浏览器里是逐字节真终端(Playwright 截图确认)。

## 发现的坑(生产必须处理)
1. **两道首启门**会吃掉第一条 prompt,新容器第一轮必卡:
   - "信任此文件夹"
   - `--dangerously-skip-permissions` 的 bypass 接受(默认选 No!)
   生产:在 `~/.claude` 预置 `hasTrustDialogAccepted` / `bypassPermissionsModeAccepted`
   等标志(或 settings),让新会话直接就绪,免交互过门。
2. **就绪握手**:发 prompt 前要确认 pane 到了 `❯` 输入框(社区的 `.ready` 模式),
   否则 prompt 落到过场界面。
3. `Auto-update failed: no write permission` 无害(npm 全局目录只读),可忽略或关自动更新。

## 后端设计(据此)
- 沙箱镜像烤入 tmux + ttyd + hooks settings.json(HTTP hook → POST 后端)+ 预接受标志。
- HTTP hook 端点按 session/turn 关联,推进 per-turn asyncio.Queue。
- 新 ComputeProvider(TmuxHooksProvider).run_turn:确保 tmux 会话 → 就绪握手 →
  paste prompt → 从 queue 取 hook 事件 → yield AgentEvent → Stop 收尾。
- 现场:每话题 ttyd,Caddy 反代,前端抽屉嵌 xterm/iframe。
- `AGENT_BACKEND=sdk|tmux` 切换,SDK 后端保留。
