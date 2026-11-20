---
name: lean
description: 用 Lean 4 写形式化证明、或给一个 Lean 项目加 CI 检查时用。含什么算证明完成、怎么装和跑 lake、怎么读编译错误、工具链怎么钉版本、CI 作业怎么配成 check run。
---

# Lean 4 形式化

## 什么时候用它

- 要把一段数学论证写成机器可检查的证明（Lean 4）。
- 要给一个 Lean 项目加 CI：编译、检查有没有未完成的证明。
- 要判断一条证明是不是真过了内核，而不是「看着像对的」。

**这台机器上默认没有 Lean。** 别假设 `lean`、`lake` 在 PATH 上，先装（见下面「装工具链」）。

## 什么算完成

一份 Lean 证明只有在**编译且不含 `sorry`** 时才叫完成。三档：

| 状态 | 判据 | 算不算完成 |
|---|---|---|
| 证明完成 | `lake build` 退出码 0，且输出里没有 `` uses `sorry` `` | 算 |
| 骨架过了、洞还在 | 编译通过（退出码 0），但有 `` declaration uses `sorry` `` 警告 | 不算 |
| 编译不过 | `lake build` 退出码 1 | 不算 |

`sorry` 是给没写完的证明占位的占位符，它靠公理 `sorryAx` 让内核放行；`admit` 是 `exact sorry` 的简写，效果一样。**编译通过不等于证完了**——带 `sorry` 的文件照样编译成功、退出码 0，这是最容易漏掉的一种。

交出去之前自己跑这一遍，把输出贴出来（以下命令在 Lean 4.35.0 上实测过）：

```bash
lake build 2>&1 | tee /tmp/lean-build.log
grep -i "sorry" /tmp/lean-build.log && echo "还有 sorry，没证完" || echo "没有 sorry"
```

要确认某条定理有没有偷用公理（`sorryAx` 会出现）：

```
#print axioms my_thm
```

写在文件里，编译时输出 `'my_thm' depends on axioms: [sorryAx]` 这样一行。

## 装工具链

用 `elan` 管版本，它按项目里的 `lean-toolchain` 自动切。

```bash
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh
```

装完新开一个 shell，然后：

```bash
lean --version      # 当前工具链版本
lake --version      # lake 是 Lean 自带的构建工具
```

**钉版本靠仓库根目录的 `lean-toolchain`，一行**：

```
leanprover/lean4:v4.35.0
```

写法就是 `leanprover/lean4:<tag>`；`v4.35.0` 是 2026-10-12 的稳定版。别把 `master` 或 `nightly-*` 写进要长期跑的仓库——nightly 自己会漂，今天绿的明天可能红。

**依赖 mathlib 时，工具链要跟 mathlib 对齐**：把 mathlib 的 `lean-toolchain` 内容抄成你自己的，否则预编译缓存对不上。声明依赖（`lakefile.toml`）之后：

```bash
lake update              # 解析依赖、写 lake-manifest.json
lake exe cache get       # 拉 mathlib 预编译产物——不跑，第一次构建要几十分钟
lake build
```

缓存奇怪时：`lake clean`（或 `rm -rf .lake`）再 `lake exe cache get` 一次。

## 怎么跑

| 你要做的 | 命令 |
|---|---|
| 构建整个包 | `lake build` |
| 只构建某个库/模块 | `lake build MyLib` （名字是 lakefile 里声明的 target，不是文件名） |
| 跑测试目标（lakefile 里声明的 `test_driver`） | `lake test` |
| 看某条定理依赖哪些公理 | `#print axioms my_thm`（写在文件里，编译时输出） |
| 清掉构建产物 | `lake clean` |

`lake build` 失败时退出码是 1（实测）。CI 就靠这个，不要自己去解析输出。

## 怎么读错误

错误打到 stderr，带 `文件:行:列` 和指示位置的行。**先改第一条**：后面的常常是它引起的连锁（一个错误会带出一串）。下面每条都是 Lean 4.35.0 上的真实输出。

| 真实输出 | 意思 | 怎么办 |
|---|---|---|
| `unsolved goals`，下面一行 `⊢ ...` | 证明没写完，`⊢` 后面是还没证的目标 | 补 tactic；这不是语法错 |
| `Unknown identifier \`X\`` | 名字不在作用域 | 看 `import` 漏了没有，或该 `open` 没 `open` |
| `Type mismatch` / `has type ... but is expected to have type ...` | 类型对不上 | 看两侧的期望类型，常是第一处错误的下游 |
| `Function expected at` | 把非函数当函数用了 | 参数个数或顺序错了 |
| `declaration uses \`sorry\``（警告，不报错） | 证明里有洞 | 补完；**CI 要当失败处理** |
| `` Tactic `rfl` failed `` / `` `simp` made no progress `` | 这条 tactic 在这步不成立 | 换 tactic 或改目标，别硬套 |
| 编译失败时最后一行 | `error: build failed` | 这是总结行，真因在上面 |

改完重跑 `lake build`——增量构建很快，别整包重来。

## 给 Lean 项目配 CI

要的目标：目标项目的 PR 上出现一个**名叫 `lean` 的 check run**，只在改动碰到 Lean 文件时才跑。

- 完整可抄的工作流在 `references/lean.yml`，直接复制到目标项目的 `.github/workflows/lean.yml`。
- 要按项目改哪几处、`required_checks` 里的路径怎么写，见 `references/ci.md`。
- 一句话：`required_checks` 按**改动的文件路径**决定这个检查是否必需，路径写错会让检查静默地不生效。

## 交付时的自证

交一份 Lean 成果时附上这三样，别人才能复查：

1. `lake build` 的结论（有没有 `sorry`），命令和输出一起。上面那段 `tee` + `grep` 就是为这个。
2. 工具链版本：`lean-toolchain` 里那一行。
3. 要人核对的部分：把主定理的陈述抄出来，让人确认「机器检查的确实是这道题」。
