"""The documentation site's server side (served at /docs/, built from docs/site).

Two things the static site cannot do by itself:

* **Developer docs are for platform admins only.** nginx asks
  ``GET /docs/dev-access/check`` before serving anything under ``/docs/dev/``;
  ``access`` issues and verifies the short-lived cookie that answers it.
* **问芝士 answers from the docs.** ``assistant`` retrieves the relevant sections
  (``retrieval``), asks the gateway's model to answer only from them, and
  streams the answer back; ``limits`` keeps it from being used as a free model.
"""
