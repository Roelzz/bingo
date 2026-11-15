"""In-memory pub/sub for Server Sent Events. Needs a single app process (one uvicorn worker)."""

import asyncio
import json


class Broker:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: dict[asyncio.Queue, int] = {}

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subs[q] = user_id
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.pop(q, None)

    def _deliver(self, payload: str, to: set[int] | None) -> None:
        for q, uid in list(self.subs.items()):
            if to is None or uid in to:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # slow client; it refetches state on reconnect anyway

    def publish(self, message: dict, to: set[int] | None = None) -> None:
        """Safe to call from worker threads (sync endpoints) and from the event loop."""
        if self.loop is None:
            return
        payload = json.dumps(message, ensure_ascii=False)
        self.loop.call_soon_threadsafe(self._deliver, payload, to)


broker = Broker()
