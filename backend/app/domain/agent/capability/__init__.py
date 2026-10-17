"""能力矩阵：把各骨架的声明汇总成一张表（`matrix.py`）。

声明用的词汇表（`BuiltIn`、`Declaration`、`Difference`、`Missing`）住在上一级
的 `app.domain.agent.declaration`：每个骨架的 `behaviour.py` 要用它写声明，
本包又要 import 那三份声明来汇总，词汇表放在任何一边都会让
`agent.harness` 与 `agent.capability` 互相 import——`domains-acyclic` 盯着的
就是这条边。
"""
