"""The two pools that serve model traffic (issue #243).

The metering proxy is the one interception point every sandbox's traffic passes
through. It has to decide, per request, where that request actually goes:

  subscription  Anthropic, on the session's own Claude credential
  gateway       an API-key pool (Zhipu / DeepSeek), reached through LiteLLM

The pool is a fact about the model, not about the project: every catalogue
choice carries the ``supply`` that serves it (`model_catalog.model_choices`),
and admission routes the request by the model binding it runs under
(`room_task/binding.py`). Admission also supplies the central account identity
for profile queries, independently of inference supply.
"""

SUBSCRIPTION = "subscription"
GATEWAY = "gateway"
