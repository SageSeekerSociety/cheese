"""The documentation site's server side (built from docs/site).

The site is static files the frontend image serves, under /docs/ on the
platform or at the root of the docs' own host (``site``). What it cannot do by
itself:

* **Know who is reading.** The platform hands its sign-in to the docs host as a
  cookie of the docs' own (``access``); 问芝士 and the developer pages read it.
* **Developer docs are for platform admins only.** nginx asks
  ``GET /docs/dev-access/check`` before serving anything under dev/.
* **问芝士 answers from the docs.** ``assistant`` retrieves the relevant sections
  (``retrieval``), asks the gateway's model to answer only from them, and
  streams the answer back; ``limits`` keeps it from being used as a free model.
"""
