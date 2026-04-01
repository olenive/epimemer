"""Async event bus for decoupled publish/subscribe.

The EventBus is the single integration point between producers (storage backend,
pipeline executor) and consumers (WebSocket relay, loggers, visualization widgets).

Producers call publish(). Consumers call subscribe() with an event type filter.
Neither side knows about the other — they only share the event types.

Usage:
    bus = create_event_bus()

    # Consumer subscribes
    unsubscribe = bus.subscribe(NodeStored, my_handler)

    # Producer publishes
    await bus.publish(NodeStored(node_id="abc", ...))

    # Consumer unsubscribes when done
    unsubscribe()
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Protocol

from epimemer.visualization.events import AnyEvent, Event, EventCategory

logger = logging.getLogger(__name__)


# --- Protocol (the contract) ---


class EventSubscriber(Protocol):
    """Callable that receives an event. Can be sync or async."""
    async def __call__(self, event: Event) -> None: ...


Unsubscribe = Any  # Callable[[], None] — returns a function to remove the subscription


class EventBusProtocol(Protocol):
    """Protocol for event bus implementations.

    This is the interface that producers and consumers depend on.
    The implementation can be swapped (in-process, Redis, etc.)
    without changing either side.
    """

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        ...

    def subscribe(
        self,
        event_type: type[Event] | None = None,
        *,
        category: EventCategory | None = None,
        handler: EventSubscriber | None = None,
    ) -> Unsubscribe:
        """Subscribe to events, filtered by type and/or category.

        Args:
            event_type: Only receive events of this exact type (or subclasses).
                None means all types.
            category: Only receive events in this category (graph or pipeline).
                None means all categories.
            handler: The async callable to invoke for each matching event.

        Returns:
            A callable that, when called, removes this subscription.
        """
        ...


# --- In-process implementation ---


class _Subscription:
    """A single subscription with its filter criteria."""

    __slots__ = ("event_type", "category", "handler")

    def __init__(
        self,
        handler: EventSubscriber,
        event_type: type[Event] | None,
        category: EventCategory | None,
    ):
        self.handler = handler
        self.event_type = event_type
        self.category = category

    def matches(self, event: Event) -> bool:
        if self.event_type is not None and not isinstance(event, self.event_type):
            return False
        if self.category is not None and event.category != self.category:
            return False
        return True


class InProcessEventBus:
    """In-process async event bus.

    All subscribers are notified concurrently via asyncio.gather.
    Subscriber errors are logged but do not propagate to publishers
    or other subscribers.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[int, _Subscription] = {}
        self._next_id = 0
        self._category_index: dict[EventCategory, set[int]] = defaultdict(set)
        self._type_index: dict[type[Event], set[int]] = defaultdict(set)
        self._global_ids: set[int] = set()

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers concurrently."""
        # Gather candidate subscription IDs from indexes
        candidate_ids: set[int] = set(self._global_ids)
        candidate_ids.update(self._category_index.get(event.category, set()))
        candidate_ids.update(self._type_index.get(type(event), set()))

        tasks = []
        for sub_id in candidate_ids:
            sub = self._subscriptions.get(sub_id)
            if sub is not None and sub.matches(event):
                tasks.append(self._safe_call(sub.handler, event))

        if tasks:
            await asyncio.gather(*tasks)

    def subscribe(
        self,
        event_type: type[Event] | None = None,
        *,
        category: EventCategory | None = None,
        handler: EventSubscriber | None = None,
    ) -> Unsubscribe:
        """Subscribe to events matching the given filters.

        Returns a callable that removes this subscription.
        """
        if handler is None:
            raise ValueError("handler is required")

        sub_id = self._next_id
        self._next_id += 1

        sub = _Subscription(handler=handler, event_type=event_type, category=category)
        self._subscriptions[sub_id] = sub

        # Index for fast lookup
        if event_type is None and category is None:
            self._global_ids.add(sub_id)
        if category is not None:
            self._category_index[category].add(sub_id)
        if event_type is not None:
            self._type_index[event_type].add(sub_id)

        def unsubscribe() -> None:
            self._subscriptions.pop(sub_id, None)
            self._global_ids.discard(sub_id)
            if category is not None:
                self._category_index[category].discard(sub_id)
            if event_type is not None:
                self._type_index[event_type].discard(sub_id)

        return unsubscribe

    @staticmethod
    async def _safe_call(handler: EventSubscriber, event: Event) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception(
                "Event handler %s failed for %s",
                getattr(handler, "__name__", handler),
                type(event).__name__,
            )


def create_event_bus() -> InProcessEventBus:
    """Create an in-process event bus.

    This is the default for single-process deployments. For distributed
    setups, swap in an implementation backed by Redis pub/sub or similar.
    """
    return InProcessEventBus()
