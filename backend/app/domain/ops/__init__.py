"""操作卡 — operation requests (第 3 步：只登记不执行).

This package is the *registry + manifest* half of the operation card: it defines
which operations exist, what each one costs if it goes wrong, and the on-disk
format an agent uses to request one. It deliberately contains **no executor**.
Execution (`pull_request_review` → run) is a later step and must start from a
read-only operation; nothing here dispatches anything.
"""
