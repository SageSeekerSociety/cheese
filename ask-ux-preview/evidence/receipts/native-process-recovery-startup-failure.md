# Archive startup failure before recovery

Fixed source: `a3a5e03a`. The first isolated busy fixture started its runner archive, which exited before native Claude startup:

```
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/tmp/pytest-of-cheese/pytest-49/test_new_full_service_process_0/runner.pyz/__main__.py", line 1, in <module>
    from app.domain.agent.harness.claude_code.entry import main
  File "/tmp/pytest-of-cheese/pytest-49/test_new_full_service_process_0/runner.pyz/app/domain/agent/harness/__init__.py", line 51, in <module>
    from app.domain.delivery.input_identity import (
    ...<3 lines>...
    )
ModuleNotFoundError: No module named 'app.domain.delivery'
```

The harness contract imports the pure input-identity module; the shared runner archive omitted it and its package. The new recovery fixture exposed this packaging defect. In-process runner tests bypassed the archive import.

The fixture's WebSocket wait only accepted its expected event and remained waiting after the launch error. The isolated test command `b5omo5yjj` was stopped via TaskStop after preserving this complete runner traceback. This is a stopped development run, not a pytest failure count or red/green acceptance run. Its capture script did not complete and produced no fixed stdout manifest. No shared runner/native session was stopped or replaced. The archive had exited before a recovery child was launched.

The next source includes input_identity.py and an empty delivery package in the standard-library-only shared archive, and the fixture explicitly rejects WebSocket error events. Original TaskStop and Read records remain in the session transcript.
