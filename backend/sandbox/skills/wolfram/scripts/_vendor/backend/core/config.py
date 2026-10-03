class Settings:
    """Fixed values, read from nowhere.

    The skill runs with somebody else's project as its working directory, on a
    machine whose environment belongs to its owner. Reading a `.env` or the
    environment here would let a variable that merely shares a name -- an
    `MCP_URL` kept for another tool -- point the skill at a different server or
    switch the safety gate off.
    """

    # ---- 符号缓存 ----
    #: 缓存文件目录（system_names.json 落在这里；wolfram_mcp.py 启动时指到状态目录）
    ASSET_DIR: str = "assets"

    # ---- Wolfram 官方 MCP（唯一执行通道）----
    #: Wolfram 托管 MCP，实测无需鉴权
    MCP_URL: str = "https://agenttools.wolfram.com/mcp"
    #: MCP 请求超时（秒）
    MCP_TIMEOUT: float = 60.0
    #: 传给 WolframLanguageEvaluator 的 timeConstraint（秒），由服务端兜底限长
    EXEC_TIMEOUT: float = 30.0
    #: 单条结果回灌时的字符上限
    RESULT_CHARS: int = 4000
    #: 单次求值最多带回的图像张数（一张约 14KB base64，不设上限会撑大响应体）
    MAX_IMAGES: int = 4

    # ---- 文档检索 ----
    #: 调用 wolfram_context 时回灌的文档字符上限
    DOC_CHARS: int = 3000

    # ---- 安全红线 ----
    SAFETY_ENFORCE: bool = True


settings = Settings()
