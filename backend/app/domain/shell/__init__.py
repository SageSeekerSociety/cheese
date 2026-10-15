"""壳 (shell): one platform, several products, no forked frontend.

`app.domain.shell_catalog` holds the declarations and the wire shape the
frontend receives — vocabulary, one layer below, because both this package and
`project` read it. `service.py` resolves which one a project runs under, which
takes `space.models`, `task.models` and `task.protocol`, so it stays here.
"""
