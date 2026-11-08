# PR #5214 录屏证据（2026-11-08，UTC）

同一场景，改前和改后各录一段真浏览器视频，看频道首屏任务卡上 Working 旁边的图标。

- 改前：主干 37767844d，后端和前端都是这一版。入口是本机 `http://127.0.0.1:13481/__accept?ws=off`，2026-11-08T23:47:20Z 录制。
- 改后：任务分支 9dcdcda5（PR #5214 当前 head），后端和前端都是这一版。入口是本机 `http://127.0.0.1:13480/__accept?ws=off`，2026-11-08T23:52:17Z 录制。
- 两个入口都自动用种子用户 alice 登录，最后落在 `/projects/chwncfow/channels/1`（「知是 2.0 融合演示」的 General 频道）。
- ws=off：实时连接握手照接但不推任何消息，页面上只有首屏 `/blocks` 的结果。
- RUNNING 是模拟的：用 `session_state.register/declare(RUNNING)` 只给「首屏标记·会话在跑（模拟）」宣告队友会话在跑。没有起真 runner，没接真模型。
- 数据：本机隔离种子库（dev-db.sh 起的 Postgres），不连任何线上数据。

## 文件
- `*-raw.webm`：Playwright（Chromium headless shell）`recordVideo` 录下的原始视频，1280×800，从打开入口开始一直录到结束，没做任何处理。
- `*-full-9s.mp4`：从原始视频里截出任务卡出现之后的 9 秒，画面没裁。
- `*-zoom4x-9s.mp4` / `.webm`：同样那 9 秒，从原始视频的同一位置裁出 320×90（左上角 x=820, y=477），再按最近邻放大 4 倍到 1280×360。只裁剪和缩放，没有合成帧。
- `*-zoom4x-t{0,1,2}s.png`：从放大视频的第 0、1、2 秒各截一帧，位置相同。
- `*-meta.json`：录制时页面里每秒读一次图标的状态，共 9 次：图标类名、是否带 data-live、animation 名和 transform。
- `SHA256SUMS.txt`：本目录所有视频和截图的 SHA256。
- `record.mjs`：录制脚本。`accept.mjs`：自动登录入口。

## 结果
- 改前：图标是 `mdi-progress-clock`，没有 data-live，animation 为 none，9 次读到的 transform 都是 none。放大帧里图标区域第 0、1、2 秒最大像素差 3（视频压缩噪声），图标静止。
- 改后：图标是 `mdi-timelapse`，带 data-live，animation 为 task-state-turn（2.4 秒一圈）。9 次读到的 transform 两两不同，放大帧里图标区域第 0 秒对第 1 秒最大像素差 147，图标一直在转。
