"""Bound model generation while keeping all database work in the main thread."""
from __future__ import annotations

import ctypes
import functools
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

MODEL_SLOTS = threading.BoundedSemaphore(2)
POLICY = {"cpu_limit_pct": 55.0, "min_free_gb": 2.0, "min_free_fraction": 0.20, "max_wait_seconds": 120.0}


def configure_model_slots(workers):
    """Configure once before launching worker threads; repairs share this cap."""
    if not 1 <= workers <= 8:
        raise ValueError("Model worker limit must be between 1 and 8")
    global MODEL_SLOTS
    MODEL_SLOTS = threading.BoundedSemaphore(workers)


def adaptive_workers(state, maximum=8):
    """Budget 0.5 GiB per model process ABOVE the existing memory reserve."""
    if not 1 <= maximum <= 8:
        raise ValueError("Adaptive maximum must be between 1 and 8")
    if not all(key in state for key in ("free_gb", "free_fraction", "cpu_pct")):
        return 0
    free, fraction, cpu = state["free_gb"], state["free_fraction"], state["cpu_pct"]
    if fraction <= 0 or fraction < POLICY["min_free_fraction"] or cpu > POLICY["cpu_limit_pct"]:
        return 0
    reserve = max(POLICY["min_free_gb"], free / fraction * POLICY["min_free_fraction"])
    memory_slots = max(0, int((free-reserve) / 0.5))
    cpu_slots = 8 if cpu <= 35 else 4
    return min(maximum, memory_slots, cpu_slots)


def resource_snapshot() -> dict:
    if os.name != "nt":
        return {"cpu_pct": 0.0, "free_gb": float("inf"), "free_fraction": 1.0, "supported": False}
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")
        ]

    def ticks():
        values = [wintypes.FILETIME() for _ in range(3)]
        if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(value) for value in values)):
            raise OSError("GetSystemTimes failed")
        return [(value.dwHighDateTime << 32) | value.dwLowDateTime for value in values]

    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError("GlobalMemoryStatusEx failed")
    start = ticks()
    time.sleep(0.2)
    end = ticks()
    idle, kernel, user = [b - a for a, b in zip(start, end)]
    total = kernel + user
    return {
        "cpu_pct": max(0.0, min(100.0, 100 * (total - idle) / max(1, total))),
        "free_gb": memory.available / 1024**3,
        "free_fraction": memory.available / max(1, memory.total), "supported": True,
    }


def wait_for_capacity(snapshot=resource_snapshot, policy=None) -> tuple[bool, dict]:
    limits = POLICY if policy is None else policy
    started = time.monotonic()
    while True:
        try:
            state = snapshot()
        except OSError:
            state = {"cpu_pct": 100.0, "free_gb": 0.0, "free_fraction": 0.0, "probe_failed": True}
        elapsed = time.monotonic() - started
        state["resource_wait_seconds"] = round(elapsed, 3)
        if state["cpu_pct"] <= limits["cpu_limit_pct"] and state["free_gb"] >= limits["min_free_gb"] and state["free_fraction"] >= limits["min_free_fraction"]:
            return True, state
        if elapsed >= limits["max_wait_seconds"]:
            return False, state
        time.sleep(min(5, limits["max_wait_seconds"] - elapsed))


def bounded_model_call(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        # Repairs and prefetch workers share the configured model-call cap.
        with MODEL_SLOTS:
            ready, state = wait_for_capacity()
            if not ready:
                return {"status": "resource_deferred", "resource_state": state}
            result = function(*args, **kwargs)
            result["resource_state"] = state
            return result
    return wrapped


def shared_generation_stream(paths, prepare, generate, workers):
    """Only generate() runs in workers. prepare() and caller's SQL run serially."""
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mimic-generation") as executor:
        pending = {}
        for index in range(min(workers, len(paths))):
            prepare(paths[index])
            pending[index] = executor.submit(generate, paths[index])
        for index, path in enumerate(paths):
            yield path, pending.pop(index).result()
            next_index = index + workers
            if next_index < len(paths):
                prepare(paths[next_index])
                pending[next_index] = executor.submit(generate, paths[next_index])


def lower_parent_priority():
    if os.name == "nt":
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        return bool(ctypes.windll.kernel32.SetPriorityClass(handle, 0x4000))
    return False


def child_flags():
    return 0x08000000 | 0x4000 if os.name == "nt" else 0


def bounded_db_env(base):
    env = dict(base)
    # Session-local reader options only; do not change server-wide settings.
    env["PGOPTIONS"] = (env.get("PGOPTIONS", "") + " -c max_parallel_workers_per_gather=0 -c jit=off").strip()
    return env
