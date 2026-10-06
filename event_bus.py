import asyncio

class EventBus:
    """Simple implementation of an event bus for decoupling components."""
    _subscribers = {} # {event_name: [callback1, callback2]}

    @staticmethod
    def subscribe(event_name: str, callback):
        if event_name not in EventBus._subscribers:
            EventBus._subscribers[event_name] = []
        EventBus._subscribers[event_name].append(callback)

    @staticmethod
    async def publish(event_name: str, *args, **kwargs):
        if event_name not in EventBus._subscribers:
            return
        # Run all callbacks concurrently
        await asyncio.gather(*(cb(*args, **kwargs) for cb in EventBus._subscribers[event_name]))