"""Which route modules this boot failed to mount.

``app/main.py`` mounts every module under ``app.api.routes`` and skips the ones
that fail to import, so one bad file cannot take the whole app down — but the
app then answers 404 for that module's whole group of endpoints, and only the
caller finds out. The readiness check turns that into a 503 so the rollout keeps
such a build away from traffic.

The list lives in core, not in ``app.main``, because two modules on opposite
sides of the layer stack need the same one: the composition root writes it while
mounting, and ``app/api/routes/health.py`` reads it. Reading it out of
``app.main`` meant the route module imported the composition root to answer
「did everything mount」, and the platform panel's health snapshot
(``app.domain.platform_stats.services``) reached through the route to do the
same — one core-level fact, imported from two layers up.
"""

# Route modules that failed to import this boot, by dotted module name. The
# composition root clears it before mounting and appends each module it skipped.
FAILED_ROUTE_MODULES: list[str] = []
