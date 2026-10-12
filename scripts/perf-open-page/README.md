# 页面打开耗时测量

量「点开一个页面到看见内容」花在哪：网络等待、主线程脚本与渲染各占多少。
结论见资料库文档《前端「点开等几秒」实测：五页数字与三个可修原因》。

## 跑起来需要什么

- 一个有界面的浏览器内核，能用调试协议驱动（本仓库开发时用的是无头 Chrome）。
- 一个已登录账号的访问令牌，放在 `ids.json`：`{"pid", "root_topic", "space", "task_number", "token", "user"}`。
  令牌不进仓库，自己登一次拿。
- `websocket-client`（`uv run --with websocket-client python measure.py` 即可）。

## 怎么用

```
PORT=9333 REPS=4 PHASES=cold,switch OUT=results.json CHROME=/path/to/chrome bash run.sh
```

`run.sh` 起浏览器、等调试端口、跑测量、关浏览器，全在一次调用里（浏览器不会跨命令存活）。
只想测某几个页面时把页名当参数传：`bash run.sh 频道,资料库`。`measure.py` 里的 `DEFER_ON` /
`DEFER_MS` 是给 A/B 用的：打开后，匹配 `DEFER_LIST` 的请求先压住若干毫秒再发，用来量
「把这批请求推迟到首帧之后」能省多少。

## 数字是怎么取的

探针在文档开始时注入（`Page.addScriptToEvaluateOnNewDocument`），从页面自己的
performance 时间线里读：

- 可见：目标路由的内容画出来、骨架屏消失、正文超过 60 字；
- 最后一份响应：`responseEnd` 的最大值，超过 10 秒的长连接不算；
- 主线程长任务：`longtask` 观察器的累计时长。

冷开是整页加载（浏览器配置目录每轮清空），切换是在同一个页面里走路由。
