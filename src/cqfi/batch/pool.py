"""Shared ProcessPoolExecutor helpers for batch engines."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor


def terminate_pool_workers(executor: ProcessPoolExecutor) -> None:
    """Force-kill worker processes so Stop actually frees the CPU.

    ``Future.cancel()`` cannot interrupt a call already handed to a worker
    (or sitting in ProcessPoolExecutor's small call queue). Waiting for those
    QuantLib batches to finish leaves the machine at full load — looking like
    Stop did nothing. Workers never write the DB (the parent process does), so
    killing them only abandons in-flight compute results.
    """
    processes = getattr(executor, "_processes", None) or {}
    for proc in processes.values():
        if proc.is_alive():
            proc.terminate()
