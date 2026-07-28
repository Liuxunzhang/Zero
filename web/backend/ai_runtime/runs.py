"""Background run lifecycle and reconnectable versioned events."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from .models import now_iso
from .storage import EventStore, atomic_json


RunCoroutine = Callable[[Callable[[str, dict[str, Any]], Awaitable[None]], asyncio.Event], Awaitable[dict[str, Any]]]


TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


@dataclass(slots=True)
class RunRecord:
    run_id: str
    conversation_id: str
    engine_id: str
    image_id: str
    status: str = "created"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    seq: int = 0
    budget: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "engine_id": self.engine_id,
            "image_id": self.image_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seq": self.seq,
            "budget": self.budget,
            "error": self.error,
        }


class RunManager:
    def __init__(self, root: Path, *, cancel_external: Callable[[str], Any] | None = None) -> None:
        self.root = Path(root)
        self.index_file = self.root / "index.json"
        self.events = EventStore(self.root / "events")
        self.events.cleanup()
        self.cancel_external = cancel_external
        self._runs: dict[str, RunRecord] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted()

    def _load_index(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.index_file.read_text("utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def _persist(self) -> None:
        persisted = {item["run_id"]: item for item in self._load_index()}
        for record in self._runs.values():
            persisted[record.run_id] = record.public()
        atomic_json(self.index_file, list(persisted.values()))

    def _recover_interrupted(self) -> None:
        items = self._load_index()
        changed = False
        for item in items:
            if item.get("status") not in TERMINAL:
                item["status"] = "interrupted"
                item["updated_at"] = now_iso()
                item["error"] = "service restarted during model or tool execution"
                run_id = str(item.get("run_id", ""))
                if run_id:
                    seq = int(item.get("last_seq", 0)) + 1
                    self.events.append(run_id, self._event(item, seq, "run_end", {
                        "status": "interrupted",
                        "reason": "service_restart",
                    }))
                    item["last_seq"] = seq
                changed = True
        if changed:
            atomic_json(self.index_file, items)

    @staticmethod
    def _event(record: RunRecord | dict[str, Any], seq: int, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        get = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
        return {
            "version": 1,
            "seq": seq,
            "run_id": get("run_id"),
            "conversation_id": get("conversation_id"),
            "timestamp": now_iso(),
            "type": event_type,
            "data": data,
        }

    def create(
        self,
        *,
        conversation_id: str,
        engine_id: str,
        image_id: str,
        execute: RunCoroutine,
        budget: dict[str, Any],
    ) -> RunRecord:
        run_id = f"run_{uuid.uuid4().hex}"
        record = RunRecord(
            run_id=run_id,
            conversation_id=conversation_id,
            engine_id=engine_id,
            image_id=image_id,
            budget=budget,
        )
        self._runs[run_id] = record
        self._persist()
        record.task = asyncio.create_task(self._drive(record, execute), name=run_id)
        return record

    async def _drive(self, record: RunRecord, execute: RunCoroutine) -> None:
        record.status = "running"
        record.updated_at = now_iso()
        self._persist()

        async def emit(event_type: str, data: dict[str, Any]) -> None:
            record.seq += 1
            record.updated_at = now_iso()
            if "budget" in data:
                record.budget = data["budget"]
            self.events.append(record.run_id, self._event(record, record.seq, event_type, data))
            async with record.condition:
                record.condition.notify_all()

        try:
            result = await execute(emit, record.cancel_event)
            record.status = str(result.get("status", "completed"))
            record.budget = result.get("budget", record.budget)
            await emit("run_end", {**result, "status": record.status})
        except asyncio.CancelledError:
            record.status = "cancelled"
            # AgentLoop may already have emitted run_end; duplicate terminal
            # events are still seq-idempotent and preserve the cancellation edge.
            await emit("run_end", {"status": "cancelled", "reason": "user_cancelled"})
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            await emit("run_end", {"status": "failed", "error": str(exc)})
        finally:
            record.updated_at = now_iso()
            self._persist()
            async with record.condition:
                record.condition.notify_all()

    def get(self, run_id: str) -> dict[str, Any] | None:
        record = self._runs.get(run_id)
        if record:
            return record.public()
        return next((item for item in self._load_index() if item.get("run_id") == run_id), None)

    async def cancel(self, run_id: str) -> bool:
        record = self._runs.get(run_id)
        if not record or record.status in TERMINAL:
            return False
        record.cancel_event.set()
        if self.cancel_external:
            result = self.cancel_external(record.engine_id)
            if asyncio.iscoroutine(result):
                await result
        if record.task and not record.task.done():
            record.task.cancel()
        return True

    async def subscribe(self, run_id: str, after_seq: int = 0) -> AsyncIterator[dict[str, Any]]:
        cursor = max(0, int(after_seq))
        while True:
            events = self.events.read(run_id, cursor)
            for event in events:
                cursor = max(cursor, int(event.get("seq", 0)))
                yield event
            record = self._runs.get(run_id)
            state = record.public() if record else self.get(run_id)
            if state is None or state.get("status") in TERMINAL:
                break
            async with record.condition:
                try:
                    await asyncio.wait_for(record.condition.wait(), timeout=15)
                except asyncio.TimeoutError:
                    # SSE keepalive is persisted nowhere and has no seq.
                    continue
