"""Reusable spawn-based workers; children compute, the parent commits files."""
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import os
import time


def worker_count(value=0):
    if type(value) is not int or value < 0:
        raise ValueError("workers 必须是大于等于 0 的整数；0 自动，1 单进程。")
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1
    count = value or available
    return min(count, 61) if os.name == "nt" else count


class ImagePool:
    def __init__(self, workers=0, progress=None):
        self.workers = worker_count(workers)
        self.progress = progress
        self.executor = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)

    def map(self, function, jobs, label="图片计算"):
        jobs = list(jobs)
        if not jobs:
            return []
        started = time.perf_counter()
        if self.workers == 1 or len(jobs) == 1:
            results = [function(job) for job in jobs]
        else:
            if self.executor is None:
                self.executor = ProcessPoolExecutor(max_workers=self.workers,
                                                    mp_context=multiprocessing.get_context("spawn"))
            futures = {self.executor.submit(function, job): i for i, job in enumerate(jobs)}
            results = [None] * len(jobs)
            last = started
            try:
                for count, future in enumerate(as_completed(futures), 1):
                    results[futures[future]] = future.result()
                    now = time.perf_counter()
                    if self.progress and now-last >= 1:
                        self.progress(f"[{label}] {count}/{len(jobs)}", flush=True)
                        last = now
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        if self.progress:
            self.progress(f"[{label}] {len(jobs)}/{len(jobs)}，{time.perf_counter()-started:.2f}s", flush=True)
        return results
