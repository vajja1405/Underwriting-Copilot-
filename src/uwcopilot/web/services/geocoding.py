"""Address lookups for real-metro sites: a thin adapter over the Studio Census Geocoder connector
(uwcopilot.studio.data.geocode), run apart from the heavy-work pool and limited per user.

Only REAL metros are geocoded (the SYNTHETIC metro has no addresses: its sites are picked on its map). The Census
Geocoder needs no key.

How a request uses it (routers/studio.py):
1. the router asks GeocodeService.prefetch for the addresses the plan needs looked up (at most 3);
2. the lookups run in this module's own small thread pool, NOT in the heavy-work pool (compute.py), so a slow or
   unreachable geocoder can never make simulations and lender files answer "busy";
3. the plan is then built with a PrefetchedGeocoder that only answers from those results (no network).

Limits (each a design choice for a small prototype, not a measured value):
- one lookup batch in flight per user (a second one gets 429 "already looking up an address");
- ADDRESS_BUDGET_BURST addresses at once per user, refilled at ADDRESS_BUDGET_PER_MINUTE (429 with the wait);
- at most MAX_WAITING_BATCHES batches waiting server-wide (503 "busy");
- one request to the Census host per MIN_INTERVAL_S seconds server-wide (Studio addendum rule F: be polite). The
  turn is reserved under a lock, but the lock is NOT held during the HTTP call;
- REQUEST_TIMEOUT_S per request and no retries: a founder waiting on a form gets a quick, plain answer.

Privacy (docs/studio/privacy.md, Studio addendum rule B): a founder's address is personal data, so
- no response cache at all: not the Studio on-disk cache (it would keep addresses in plain text under
  data/studio/cache) and not a shared memory cache (it would keep an address after its plan is deleted, and would let
  another user learn by timing that an address was entered);
- the match (coordinates, tract, matched address and the SourceRecord with the request date and the response
  checksum) is stored inside the founder's encrypted plan, and a PUT that keeps an address reuses it.
"""

import asyncio
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from uwcopilot.studio.data.geocode import HOST, GeocodeMatch, geocode_address
from uwcopilot.studio.data.http import HostPolicy, PoliteClient
from uwcopilot.web.security import RateLimiter
from uwcopilot.web.services.errors import ServiceError, Unavailable

MIN_INTERVAL_S = 1.0
REQUEST_TIMEOUT_S = 10.0
WORKERS = 2
MAX_WAITING_BATCHES = 8
ADDRESS_BUDGET_PER_MINUTE = 2
ADDRESS_BUDGET_BURST = 9
# How long a request waits for its batch: every address may take REQUEST_TIMEOUT_S plus its turn.
BATCH_WAIT_S = 45.0


class TooManyRequests(ServiceError):
    """The user is looking up addresses too fast (429)."""

    status_code = 429


class Geocoder(Protocol):
    """Turns a one-line US address into a GeocodeMatch (raises the Studio data errors on failure)."""

    def geocode(self, address: str) -> GeocodeMatch:
        """Look up one address."""
        ...


class HostSpacer:
    """Hands out request turns at least interval_s apart. Reserving a turn takes the lock for a moment; the caller
    then sleeps until its turn without holding it."""

    def __init__(
        self,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """interval_s between two requests; clock and sleep are replaceable in tests."""
        self.interval_s = interval_s
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._next_free = 0.0

    def wait_for_turn(self) -> None:
        """Reserve the next free turn, then sleep (lock released) until it comes."""
        with self._lock:
            now = self.clock()
            start = max(now, self._next_free)
            self._next_free = start + self.interval_s
        if start > now:
            self.sleep(start - now)


def census_client() -> PoliteClient:
    """A PoliteClient for the Census host: no response cache, a short timeout, no retries. Spacing between requests
    is done by HostSpacer (thread-safe), so the client's own per-host interval is switched off."""
    policy = HostPolicy(min_interval_s=0.0, max_retries=0, timeout_s=REQUEST_TIMEOUT_S)
    return PoliteClient(cache=None, policies={HOST: policy})


class CensusGeocoder:
    """The Census Geocoder, spaced MIN_INTERVAL_S apart server-wide, with no cache of any kind."""

    def __init__(self, client: PoliteClient | None = None, spacer: HostSpacer | None = None) -> None:
        """client: a PoliteClient (tests pass one with an httpx.MockTransport); None builds census_client()."""
        self.client = client if client is not None else census_client()
        self.spacer = spacer if spacer is not None else HostSpacer(MIN_INTERVAL_S)

    def geocode(self, address: str) -> GeocodeMatch:
        """The match for an address. NotAvailable when the geocoder finds none; HostUnreachable / UpstreamError
        when it cannot be asked."""
        self.spacer.wait_for_turn()
        return geocode_address(self.client, address)


GeocodeOutcome = GeocodeMatch | Exception


class PrefetchedGeocoder:
    """A Geocoder that answers only from lookups already made (it never touches the network)."""

    def __init__(self, outcomes: dict[str, GeocodeOutcome] | None = None) -> None:
        """outcomes: {address: match, or the exception its lookup raised}."""
        self.outcomes = dict(outcomes or {})

    def geocode(self, address: str) -> GeocodeMatch:
        """The prefetched match, or the prefetched error raised again."""
        outcome = self.outcomes.get(address)
        if outcome is None:
            raise Unavailable("the address was not looked up; please submit the plan again")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class GeocodeService:
    """Runs address lookups in a small pool of their own, with the per-user and server-wide limits above."""

    def __init__(
        self,
        geocoder: Geocoder | None = None,
        workers: int = WORKERS,
        max_waiting: int = MAX_WAITING_BATCHES,
        wait_s: float = BATCH_WAIT_S,
        budget: RateLimiter | None = None,
    ) -> None:
        """geocoder: None = CensusGeocoder(). budget: per-user address budget (tests pass their own)."""
        self.geocoder: Geocoder = geocoder if geocoder is not None else CensusGeocoder()
        self.max_waiting = max_waiting
        self.wait_s = wait_s
        self.budget = budget if budget is not None else RateLimiter(ADDRESS_BUDGET_PER_MINUTE, ADDRESS_BUDGET_BURST)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="uwc-geocode")
        self._lock = threading.Lock()
        self._busy_owners: set[str] = set()
        self._waiting = 0

    def lookup_all(self, addresses: list[str]) -> dict[str, GeocodeOutcome]:
        """Look every address up in turn (runs in this service's pool); errors are kept, not raised."""
        outcomes: dict[str, GeocodeOutcome] = {}
        for address in addresses:
            try:
                outcomes[address] = self.geocoder.geocode(address)
            except Exception as error:  # noqa: BLE001 - kept and raised again by PrefetchedGeocoder, per address
                outcomes[address] = error
        return outcomes

    def _admit(self, owner: str, count: int) -> None:
        """Refuse the batch when the user has one in flight, is over budget, or the server is full."""
        with self._lock:
            if owner in self._busy_owners:
                raise TooManyRequests("an address lookup of yours is still running; wait for it to finish")
            if self._waiting >= self.max_waiting:
                raise Unavailable("the address lookup is busy right now; please try again in a minute")
            for _ in range(count):
                wait = self.budget.check("geocode:" + owner)
                if wait > 0:
                    raise TooManyRequests(
                        f"you have looked up many addresses in a short time; try again in {wait:.0f} seconds"
                    )
            self._busy_owners.add(owner)
            self._waiting += 1

    def _release(self, owner: str) -> None:
        """The user's batch is finished (or abandoned after the wait)."""
        with self._lock:
            self._busy_owners.discard(owner)
            self._waiting -= 1

    async def prefetch(self, owner: str, addresses: Iterable[str]) -> PrefetchedGeocoder:
        """Look up the addresses (duplicates once) for one user; a PrefetchedGeocoder with the outcomes."""
        unique = list(dict.fromkeys(addresses))
        if not unique:
            return PrefetchedGeocoder()
        self._admit(owner, len(unique))
        future = self._executor.submit(self.lookup_all, unique)
        future.add_done_callback(lambda _: self._release(owner))
        try:
            outcomes = await asyncio.wait_for(asyncio.wrap_future(future), timeout=self.wait_s)
        except TimeoutError as error:
            raise Unavailable("the Census Geocoder is not answering in time; please try again later") from error
        return PrefetchedGeocoder(outcomes)

    def shutdown(self) -> None:
        """Stop the pool (running lookups finish in the background)."""
        self._executor.shutdown(wait=False, cancel_futures=True)
