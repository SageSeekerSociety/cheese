# 导览浮窗不再压住正文 —— 证据（2026-10-23）

`fix(onboarding): the guide bubble stops covering text and re-measures while a page animates`
那一笔的改前/改后截图和运行记录。**版面是合成的**（一个被圈住的按钮加几列正文，
不是产品里真的那条导览）：happy-dom 不排版，单测里每个框量出来都是 0×0，看不见
一个矩形压住另一个，所以这件事只能在真浏览器里量。四条 `e2e/tests/
guide-bubble-does-not-cover-text.spec.ts` 用的也是同一个合成版面。

## 截图

同一个页面、同一份构建，只把浮窗组件换成改前那一版和改后这一版，各截一张。
「有地方可放」是 3 列正文、下方留空；「没地方可放」是 6 列铺满、只剩底部一条比
浮窗还窄的空带，此时任何位置都会压到字，只看压得最少的那一处落在哪。

| 情形 | 改前 | 改后 |
|---|---|---|
| 有地方可放 | `before-room-to-place.png` —— 浮窗压在正文上 | `after-room-to-place.png` —— 落到下方空白 |
| 确实没地方 | `before-no-room.png` —— 压在正文上 | `after-no-room.png` —— 落到压得最少的那处 |

面积按「浮窗矩形与每一行正文文字矩形相交部分」逐行累加，单位是像素²：

| 情形 | 改前 | 改后 | 全窗口最小可能值 |
|---|---|---|---|
| 有地方可放 | 32,640 | 0 | 0 |
| 确实没地方 | 32,800 | 4,800 | 4,800 |

改前那一版走的是一条更早的规则：先试目标右边，再一层层往外试上下，哪儿都压着
就往目标旁边一放。它只把「能点的控件」当成不能压的东西，正文不算，所以 32,000
多像素² 全落在字上。

## 运行记录

`red-run.txt` / `green-run.txt` 是同一组检查对改前、改后两次的完整输出。改前两条
都失败（第一条期望压 0、实得 32,640；第二条期望不超过最小的 4,800、实得 32,800），
改后两条都通过。

跑法（本地，需要先起 `frontend` 的 dev server，并把 e2e 依赖装上）：

```bash
cd e2e
./node_modules/.bin/playwright test guide-bubble-does-not-cover-text.spec.ts
```

这条检查按 `frontend/AGENTS.md` 的说法只跑真浏览器：DOM 测试没有排版。CI 上它在
e2e 套件里（合并后跑），改动触及的前端套件在 PR 自己的 Required CI 里。
