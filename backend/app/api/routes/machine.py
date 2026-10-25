"""What a session's sandbox may ask of the platform.

A sandbox holds no credential. Its request reaches these routes only through
the process that owns the device links (``app.api.machine_relay``), which names
the device and the state directory the request arrived on and vouches for both
with the device connection secret; the caller is whichever session holds that
state now (``app.api.machine_caller``). A model call it makes is served by
the model route (``llm_proxy``, ``/machine/llm``).
"""

from fastapi import APIRouter

from app.api.machine_caller import Caller

router = APIRouter(prefix="/machine", tags=["machine"], include_in_schema=False)


@router.get("/whoami")
async def whoami(caller: Caller) -> dict:
    """Whose this sandbox is, as the platform sees it from the socket alone."""
    return {
        "session": caller.session_id,
        "project": caller.project_id,
        "conversation": caller.conversation_id,
        "agent": caller.agent_handle,
    }
