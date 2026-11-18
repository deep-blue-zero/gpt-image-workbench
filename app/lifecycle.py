"""Keep the launch command alive while a requested restart loads a fresh interpreter."""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from multiprocessing.connection import wait
from pathlib import Path


@dataclass(frozen=True)
class RestartTarget:
    data_dir: Path
    port: int
    instance: str


def _run(worker, args, control):
    # The supervisor closes its end to request shutdown. A lost parent signals EOF too.
    with control:
        try:
            # wait() also treats a broken Windows pipe as ready. Tk timer callbacks
            # must receive a shutdown signal when the launcher disappears.
            result = worker(args, lambda: bool(wait([control], timeout=0)))
        except KeyboardInterrupt:
            return
        try:
            control.send(result)
        except (EOFError, OSError):
            pass  # The supervisor already exited.


def supervise(worker, args):
    context = multiprocessing.get_context("spawn")
    args.restarted_from = None
    while True:
        parent, child = context.Pipe()
        process = context.Process(target=_run, args=(worker, args, child))
        process.start()
        child.close()
        target = None
        try:
            with parent:
                while not wait([parent], timeout=0.25):
                    pass  # A bounded wait lets the launching terminal deliver Ctrl+C on Windows.
                target = parent.recv()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            process.join()
        if process.exitcode:
            raise SystemExit(process.exitcode)
        if target is None:
            return
        args.data_dir, args.port, args.restarted_from = target.data_dir, target.port, target.instance
