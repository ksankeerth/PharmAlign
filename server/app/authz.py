"""can_act runs on every operation, before its handler."""
from dataclasses import replace
from typing import Optional

from pharmalign_contracts.spec import R

from .context import Tenant
from .identity import Actor
from .problems import forbidden, problem
from .roles import same_group


def acting_view(route: R, view: Optional[str], two_roles: bool) -> Optional[str]:
    """The view a person uses for this route: their own if it is allowed, else one from their group. None if neither."""
    if view in route.views:
        return view
    if two_roles:
        return next((v for v in route.views if same_group(view, v)), None)
    return None


def can_act(route: R, actor: Optional[Actor], tenant: Optional[Tenant], env: str, two_roles: bool = False) -> Optional[Actor]:
    """Raises the problem that refuses this call, and returns the actor with the view used for it, which is what the
    handlers and the audit log see. Separation-of-duty rules are guards in the handlers."""
    if route.public:
        return actor
    if actor is None:
        problem(401, "not_signed_in", "Not signed in", "This operation needs a session.")
    in_simulation = env == "simulation" and tenant.mode == "simulation"
    if route.views:
        acting = acting_view(route, actor.view, two_roles)
        if acting is None:
            view = (actor.view or actor.type).replace("_", " ")
            forbidden(f"{route.summary}: not allowed for the {view} view.")
        actor = replace(actor, view=acting)
    elif actor.type == "person":
        # Operations with no views are for a named non-person caller. Some allow simulation to stand in.
        if not (in_simulation and "simulation" in (route.actor or "")):
            forbidden(f"{route.summary}: only {route.actor or 'a service'} may call this.")
    if route.simulation_only and not in_simulation:
        problem(403, "simulation_only", "Simulation only", f"{route.summary} is refused outside simulation.")
    return actor
