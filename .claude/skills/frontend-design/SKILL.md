---
name: frontend-design
description: >
  前端设计体检：把一处改动或一个页面对照 docs/design-system.md 逐条过一遍——
  写死的颜色、跑偏的圆角字号、hover 位移、transition: all、关不掉的循环动画、
  装饰性动画、界面中文文案。输出踩了哪几条、在哪几个文件，不自动改。
  TRIGGER when: 用户说体检 / 检查设计 / 这个页面对不对 / 符不符合设计系统 /
  改完前端想确认没走样；或者要动 frontend/ 下的样式与文案而想先看基线。
  SKIP: 后端、纯逻辑改动、以及只想跑闸门的情况（那直接跑 lint:style 就够）。
---

# 前端设计体检

规范全文：[`docs/design-system.md`](../../../docs/design-system.md)。改 `frontend/**` 时
[`.claude/rules/frontend.md`](../../rules/frontend.md) 会自动加载，那是"写的时候别踩"；
**这个 skill 是"写完之后过一遍"**，两者不重复。

## 为什么需要它

规范里能上闸门的部分已经上了：写死的颜色、跑偏的圆角有 stylelint，Vuetify 固定调色板名有
`check-repo-rules.sh`。**剩下的全是机器判不了的**——一个动画在说什么、一句中文像不像人话、
一个 hover 该不该动位置。那部分没有闸门，只能靠有人真的去看一遍。

所以这个 skill 的价值不在"跑命令"，在**它列全了要看的东西**：漏掉的那条永远是没人想起来的那条。

## 先确定范围

```bash
git fetch origin main                                      # 先追上，见下
git diff --name-only origin/main...HEAD -- 'frontend/**'   # 一次改动
# 或者由用户点名一个页面/组件
```

**用 `origin/main`，不要用 `main`。** 本地 `main` 只在同步上游时才前进，平时落后好几个提交；
拿它当基线会把别人早就合并的东西一并算成"这次的改动"，清单瞬间从十几行涨到几百行，然后
没人会读完。

范围大时先跑第 1 步（有闸门的），它会直接告诉你有没有新增违规；范围小时直接从第 2 步开始读代码。

## 1. 有闸门的部分（先跑，几秒钟）

```bash
cd frontend
pnpm run lint:style                        # 写死颜色 + 圆角档位，棘轮只拦新增
cd .. && bash .claude/scripts/check-repo-rules.sh   # Vuetify 固定调色板名（stylelint 看不见模板）
```

两个都绿只说明**没有新增**。存量冻在基线里，所以"绿"不等于这个文件干净——你正在读的这个
文件很可能就在基线里，别照着它抄。基线在 `frontend/stylelint-baseline.json` 和
`frontend/palette-baseline.json`，想知道手上这个文件有没有额度就去查它。

## 2. 动效（规范 §9，没有闸门）

### 2.1 hover 里有没有位移

```bash
cd frontend
python3 - <<'PY'
import re, pathlib
for p in sorted(pathlib.Path('src').rglob('*')):
    if p.suffix not in ('.vue', '.css', '.scss') or not p.is_file():
        continue
    s = p.read_text(encoding='utf-8', errors='ignore')
    for m in re.finditer(r'([^{}]*:hover[^{}]*)\{([^{}]*)\}', s):
        body = m.group(2)
        if not re.search(r'transform\s*:\s*[^;]*translate[XY]?\(\s*-?[0-9.]+', body):
            continue
        # translate(-50%) 是居中定位，不是抬升
        if 'translateY(-50%)' in body or 'translateX(-50%)' in body:
            continue
        print(f'{p}: {m.group(1).strip()}')
PY
```

**判据**：hover 只改颜色，不改位置（§9.1）。命中就是要改的——除非它在一个一屏只有三五个大卡片
的页面上，那也仍然要改，因为同一个产品的两半用两套动作语言，人感觉得到。

### 2.2 `transition: all`

```bash
cd frontend && grep -rn "transition: all" src/
```

**判据**：一条都不该有（§9.4）。`all` 会把布局属性一起带上，在列表里每帧重排；而且读代码的人
看不出它想让什么动。改成具体属性。

### 2.3 关不掉的循环动画

```bash
cd frontend
comm -23 <(grep -rl "infinite" src/ --include=*.vue --include=*.css --include=*.scss | sort) \
         <(grep -rl "prefers-reduced-motion" src/ | sort)
```

**判据**：凡是 `infinite`，自己要写一条 `@media (prefers-reduced-motion: reduce) { animation: none }`。
`style.css` 那条全局兜底把时长压到 `0.001ms`，对无限循环等于把呼吸变成高频闪烁，比不管更糟（§9.5）。

再问一句：**关掉动画之后，它表达的信息还在吗？** 不在的话，一开始就不该只用动来表达。

### 2.4 时长有没有跑出三档

```bash
cd frontend
grep -rhoE "(transition|animation)[^;]*?[0-9.]+m?s" src/ --include=*.vue --include=*.css --include=*.scss \
  | grep -oE "[0-9.]+m?s" | sort | uniq -c | sort -rn
```

**判据**：0.12（回应指针）/ 0.2（出现消失）/ 0.3（整块进出）。缓动默认 `ease`，循环用
`ease-in-out`，`linear` 只给真匀速的。

### 2.5 没人碰也在动的

人工看 2.3 列出的那些文件：**这个动画在说什么？** 说不出正在发生的任何一件事就是装饰，删掉。
加载骨架的扫光、运行中的呼吸点、进度条说得出；上下浮动的图形、永远流动的渐变、纯装饰的入场
淡入说不出（§9.6）。

## 3. 排版与密度

### 3.1 `.t-meta` 有没有拿去写中文

```bash
cd frontend && grep -rn 't-meta' src/ --include=*.vue | grep -P '[\x{4e00}-\x{9fff}]'
```

`.t-meta` 是等宽字体（JetBrains Mono），给时间戳、id、计数用的。**中文没有等宽字形**，一段中文
落在它上面会掉到 fallback 字体，和周围的正文不是一套。命中的地方要分辨：这一行里的中文是标签
（"共"、"件"这种夹在数字之间的量词，可以留）还是一个句子（要换成 `.t-body` 或 `.t-meta` 只包数字那一段）。

### 3.2 字号有没有低于可读下限

```bash
cd frontend && grep -rnE "font-size:\s*(10|10\.5|11|11\.5|12)px" src/ --include=*.vue --include=*.css
```

**判据**：正文下限 13px。10–11px 是给熟练用户的密度，对这个产品要服务的零基础同学是压力。
纯装饰性角标除外。半像素（10.5 / 11.5 / 12.5）一律不该新增。

### 3.3 分隔点堆叠

```bash
cd frontend && grep -rn '·' src/ --include=*.vue | grep -vE "board__sep|\.t-meta" | head -30
```

这一条**不是禁令**：`A · B · C` 是这套界面里正当的元信息写法（看板卡片、消息元信息都在用）。
要看的是**同一屏里有没有堆成模式**——三处以上并排的中间点串，读起来就从"信息"变成"排版习惯"。
拿不准就跳过这条，它是四条里最主观的。

## 4. 文案（规范 §8，完全没有闸门）

对着 §8.0 那张正反例表看一遍改动里所有会被用户看见的中文。最常漏的四条：

- **空态**一律「暂无 X」，不带句号。
- **界面上不出现实现术语**：跑沙箱 / 干活 / 派活 → 运行任务；算力节点 / 连接器 → 设备；
  小队 → 团队。判据是"第一次用这个产品的人看得懂吗"。
- **括号里不解释内部机制**：`理由（会留在卡上）` → `理由`。括号只装一个短限定，不装句子。
- **人称一律「你」**，不用「您」。

```bash
git diff origin/main...HEAD -- 'frontend/**' | grep -E '^\+' | grep -P '[\x{4e00}-\x{9fff}]'
```

**删 UI 元素比改文字风险高**（§8.7）：拿不准某个元素是不是纯 meta，保留并提出来问，不要自己删。

## 5. 报告

输出格式：**每条一行——踩了哪一条、在哪个文件哪一行、建议怎么改**。分成两组：

- **要改的**：违反规范且有具体后果的。
- **提出来问的**：判断题，或者改了会动到产品决策的（比如删一个元素、改一处文案的语气）。

不要自动改。这个 skill 的产出是一份清单，改不改、怎么改由人定——尤其第 3.3 和第 4 节那些
判断题，机器判错的代价比漏判高。
