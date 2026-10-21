from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TransferTask:
    user_id: int
    chat_id: int
    source_kind: str
    source_message: Any
    status_message: Any
    source_url: str | None = None
    username: str | None = None
    display_name: str | None = None
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    filename: str | None = None
    size: int | None = None
    temp_path: str | None = None
    download_complete: bool = False
    part_paths: list[str] = field(default_factory=list)
    part_urls: dict[int, str] = field(default_factory=dict)
    txt_generating: bool = False
    error: str | None = None
    cancel_requested: bool = False

    def set_state(self, state: str) -> None:
        self.state = state
        self.updated_at = time.time()


class FairTaskQueue:
    """Colas FIFO privadas por usuario con rotación justa entre usuarios."""

    def __init__(self) -> None:
        self.jobs: dict[str, TransferTask] = {}
        self.queues: dict[int, deque[str]] = defaultdict(deque)
        self.rotation: deque[int] = deque()
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()

    async def enqueue(self, job: TransferTask, per_user_limit: int) -> bool:
        async with self.lock:
            user_jobs = [j for j in self.jobs.values() if j.user_id == job.user_id and j.state in {"queued", "downloading", "uploading", "cancelling", "paused"}]
            if len(user_jobs) >= per_user_limit:
                return False
            self.jobs[job.task_id] = job
            queue = self.queues[job.user_id]
            if not queue:
                self.rotation.append(job.user_id)
            queue.append(job.task_id)
            self.wakeup.set()
            return True

    async def take(self) -> TransferTask:
        while True:
            await self.wakeup.wait()
            async with self.lock:
                while self.rotation:
                    user_id = self.rotation.popleft()
                    queue = self.queues[user_id]
                    if not queue:
                        self.queues.pop(user_id, None)
                        continue
                    task_id = queue.popleft()
                    if queue:
                        self.rotation.append(user_id)
                    else:
                        self.queues.pop(user_id, None)
                    job = self.jobs.get(task_id)
                    if job is None or job.cancel_requested:
                        if not self.rotation and not any(self.queues.values()):
                            self.wakeup.clear()
                        continue
                    job.set_state("downloading")
                    if not self.rotation and not any(self.queues.values()):
                        self.wakeup.clear()
                    return job
                self.wakeup.clear()

    async def requeue(self, job: TransferTask) -> None:
        async with self.lock:
            job.cancel_requested = False
            job.error = None
            job.set_state("queued")
            queue = self.queues[job.user_id]
            if not queue:
                self.rotation.append(job.user_id)
            queue.append(job.task_id)
            self.wakeup.set()

    async def cancel(self, task_id: str, user_id: int) -> TransferTask | None:
        async with self.lock:
            job = self.jobs.get(task_id)
            if not job or job.user_id != user_id or job.state not in {"queued", "downloading", "uploading"}:
                return None
            job.cancel_requested = True
            queue = self.queues.get(user_id)
            if queue and task_id in queue:
                job.set_state("paused")
                queue.remove(task_id)
                if not queue:
                    self.queues.pop(user_id, None)
                    try:
                        self.rotation.remove(user_id)
                    except ValueError:
                        pass
            else:
                job.set_state("cancelling")
            return job

    def for_user(self, user_id: int, states: set[str] | None = None) -> list[TransferTask]:
        items = [j for j in self.jobs.values() if j.user_id == user_id and (states is None or j.state in states)]
        return sorted(items, key=lambda j: j.created_at, reverse=True)

    async def delete(self, task_id: str, user_id: int) -> TransferTask | None:
        async with self.lock:
            job = self.jobs.get(task_id)
            if not job or job.user_id != user_id:
                return None
            if job.state in {"queued", "downloading", "uploading", "cancelling"}:
                return None
            self.jobs.pop(task_id, None)
            return job
