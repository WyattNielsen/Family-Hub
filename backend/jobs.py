"""Named fire-and-forget tasks. A name that is already running is not started again."""

import asyncio
import traceback

_jobs: dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()


async def run_background(name: str, factory) -> dict:
    """Schedule factory() if `name` is idle.

    factory is a zero-arg callable that returns a coroutine. It is called only
    when a job is actually started. This returns as soon as the task is
    scheduled; it does not wait for the job.
    """
    async with _lock:
        running = _jobs.get(name)
        if running is not None and not running.done():
            return {"name": name, "started": False}
        coro = factory()
        try:
            task = asyncio.create_task(_guard(name, coro), name=name)
        except Exception:
            coro.close()
            raise
        _jobs[name] = task
    return {"name": name, "started": True}


async def _guard(name: str, coro):
    try:
        await coro
    except Exception:
        print(f"Background job {name} error:\n{traceback.format_exc()}", end="")
    finally:
        async with _lock:
            if _jobs.get(name) is asyncio.current_task():
                _jobs.pop(name, None)
