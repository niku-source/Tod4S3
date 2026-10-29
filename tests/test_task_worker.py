import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import bot
from task_queue import FairTaskQueue, TransferTask


def test_worker_marks_unexpected_failure_and_continues_to_next_task():
    first = TransferTask(1, 1, "url", None, None)
    second = TransferTask(2, 2, "url", None, None)

    class FakeManager:
        def __init__(self):
            self.pending = [first, second]

        async def take(self):
            return self.pending.pop(0)

    application = SimpleNamespace(bot_data={"task_queue": FakeManager()})
    processed = []

    async def process(job, _application):
        processed.append(job.task_id)
        if job is first:
            raise RuntimeError("simulated unexpected worker error")
        raise asyncio.CancelledError

    async def run_worker():
        try:
            await bot.task_worker(application)
        except asyncio.CancelledError:
            return
        raise AssertionError("El test esperaba cancelar el worker tras la segunda tarea")

    with patch.object(bot, "process_task", side_effect=process):
        asyncio.run(run_worker())

    assert processed == [first.task_id, second.task_id]
    assert first.state == "error"
    assert "RuntimeError" in (first.error or "")


def test_ensure_task_workers_replaces_a_finished_worker():
    async def scenario():
        async def finish():
            return None

        finished_worker = asyncio.create_task(finish())
        await finished_worker
        application = SimpleNamespace(bot_data={
            "task_queue": FairTaskQueue(),
            "task_worker_count": 1,
            "task_workers": [finished_worker],
        })

        assert bot.ensure_task_workers(application) == 1
        replacement = application.bot_data["task_workers"][0]
        assert not replacement.done()
        replacement.cancel()
        await asyncio.gather(replacement, return_exceptions=True)

    asyncio.run(scenario())


def test_shutdown_marks_queued_tasks_as_interrupted():
    job = TransferTask(1, 1, "url", None, None, source_url="https://example.org/file")

    class FakeStatusMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    status_message = FakeStatusMessage()
    job.status_message = status_message
    application = SimpleNamespace(bot_data={
        "task_queue": SimpleNamespace(jobs={job.task_id: job}),
    })

    interrupted = asyncio.run(bot.mark_unfinished_tasks_interrupted(application))

    assert interrupted == 1
    assert job.state == "error"
    assert "Vuelve a enviar el enlace" in (job.error or "")
    assert len(status_message.edits) == 1
    assert "ᴠᴜᴇʟᴠᴇ ᴀ ᴇɴᴠɪᴀʀ" in status_message.edits[0][0]
