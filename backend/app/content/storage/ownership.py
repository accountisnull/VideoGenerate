"""操作系统文件锁：进程退出自动释放，防止第二实例误恢复活跃作业。"""

import os
from pathlib import Path
from typing import BinaryIO


class ServiceOwnership:
    def __init__(self, database: Path) -> None:
        self.path = database.with_suffix(database.suffix + ".lock")
        self.file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("内容数据库已有服务占用，拒绝启动第二实例") from None
        self.file = handle

    def release(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
