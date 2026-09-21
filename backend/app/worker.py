import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from . import runtime
from .media import self_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@contextmanager
def worker_lock():
    import msvcrt

    runtime.DATA.mkdir(parents=True, exist_ok=True)
    with (runtime.DATA / "worker.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def execute_check(check):
    try:
        output, detail = self_check(
            runtime.DATA / "assets" / "diagnostics" / check["id"], runtime.media_tools()
        )
        runtime.finish_check(check["id"], "succeeded", detail, str(output.relative_to(runtime.DATA)))
    except Exception as error:
        logger.exception("Environment check failed")
        runtime.finish_check(check["id"], "failed", str(error))


def main():
    runtime.initialize()
    with worker_lock(), ThreadPoolExecutor(max_workers=1) as pool:
        runtime.recover_checks()
        logger.info("Worker started; environment checks only")
        pending = None
        while True:
            runtime.heartbeat()
            if pending is None or pending.done():
                if pending:
                    pending.result()
                check = runtime.claim_check()
                pending = pool.submit(execute_check, check) if check else None
            time.sleep(1)


if __name__ == "__main__":
    main()
