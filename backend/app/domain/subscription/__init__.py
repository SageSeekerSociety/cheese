"""平台级 LLM 订阅（v1：ChatGPT / OpenAI Codex OAuth）的家。

一个导入的 ChatGPT 订阅按「网关池里的订阅型上游」接入（mimo 先例）：后端在网关
上维护一条运行时模型，它的 `api_key` 就是订阅的 access_token；token 的完整生命
周期（device flow 导入、加密落库、到期刷新、刷新后推进网关）由这个域全权管理。

不复用 `user_o_auth_connection`：那张表的语义是「一个用户的登录连接」，会被
`integrations.oauth_health` 当成登录健康度统计；平台级订阅是共享供给凭据，混进
去会让两个口径都失真（见迁移 docstring）。
"""
