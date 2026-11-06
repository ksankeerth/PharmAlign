"""can_act runs on every operation, before its handler."""
from typing import Optional

from pharmalign_contracts.spec import R

from .context import Tenant
from .identity import Actor
from .problems import forbidden, problem


def can_act(route: R, actor: Optional[Actor], tenant: Optional[Tenant], env: str) -> None:
    """Raises the problem that refuses this call. Separation-of-duty rules are guards in the handlers."""
    if route.public:
        return
    if actor is None:
        problem(401, "not_signed_in", "Not signed in", "This operation needs a session.")
    in_simulation = env == "simulation" and tenant.mode == "simulation"
    if route.views:
        if actor.view not in route.views:
            view = (actor.view or actor.type).replace("_", " ")
            forbidden(f"{route.summary}: not allowed for the {view} view.")
    elif actor.type == "person":
        # Operations with no views are for a named non-person caller. Some allow simulation to stand in.
        if not (in_simulation and "simulation" in (route.actor or "")):
            forbidden(f"{route.summary}: only {route.actor or 'a service'} may call this.")
    if route.simulation_only and not in_simulation:
        problem(403, "simulation_only", "Simulation only", f"{route.summary} is refused outside simulation.")
