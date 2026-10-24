"""The sealer's, the retention sweeps' and the retired 现场 archive's clocks."""

from app.core.background import PeriodicJobs
from app.core.db import SessionFactory
from app.domain.session_record import retention, retired_site, service


def register_periodic_jobs(jobs: PeriodicJobs, *, sessions: SessionFactory) -> None:
    jobs.register(
        "session record seal",
        service.SEAL_EVERY_S,
        lambda: service.seal(sessions),
    )
    jobs.register(
        "session record retention",
        retention.EVERY_S,
        lambda: retention.sweep(sessions),
    )
    jobs.register(
        "retired site archive",
        retired_site.EVERY_S,
        retired_site.RetiredSiteArchive(sessions).run,
    )
