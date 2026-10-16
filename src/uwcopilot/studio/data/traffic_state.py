"""State DOT traffic counts: one adapter per state, used where a documented dataset improves on FHWA HPMS.

The Studio plan names the states of the v1 metros (NY, NJ, CA, IL, IN, TX; PA only if a metro includes a PA county).
No state DOT dataset has been identified and documented for this project yet, so every adapter raises NotAvailable
with the reason, and SiteIQ uses the national HPMS layer (traffic_hpms.py) instead. Adding a state = writing its
fetch function here and setting `dataset` (URL, terms, update frequency) once the dataset is opened and recorded.
"""

from collections.abc import Callable
from dataclasses import dataclass

from uwcopilot.studio.data.errors import NotAvailable
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.traffic_hpms import NearestRoad


@dataclass(frozen=True)
class StateTrafficAdapter:
    """One state's traffic-count adapter. dataset is None until a documented dataset is recorded."""

    state: str
    dataset: str | None = None
    fetch: Callable[[PoliteClient, float, float], NearestRoad] | None = None

    def nearest_count(self, client: PoliteClient, lat: float, lon: float) -> NearestRoad:
        """The nearest state DOT count station or segment; NotAvailable while no dataset is recorded."""
        if self.fetch is None or self.dataset is None:
            raise NotAvailable(
                f"no documented {self.state} DOT traffic-count dataset has been recorded yet; "
                "the national FHWA HPMS layer is used instead"
            )
        return self.fetch(client, lat, lon)


ADAPTERS: dict[str, StateTrafficAdapter] = {
    state: StateTrafficAdapter(state=state) for state in ("NY", "NJ", "PA", "CA", "IL", "IN", "TX")
}


def adapter_for(state: str) -> StateTrafficAdapter:
    """The adapter for a state; NotAvailable for a state without one."""
    if state not in ADAPTERS:
        raise NotAvailable(f"no traffic adapter for state {state!r}; the national FHWA HPMS layer is used")
    return ADAPTERS[state]
