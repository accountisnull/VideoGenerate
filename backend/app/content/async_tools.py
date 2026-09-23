"""等待同步索引操作结束后传播取消，避免后台线程继续占用或写入索引。"""

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def bounded_thread(call: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # 已开始的数据库请求不能撤销；等待其有限超时，结果仍须核对。
        await asyncio.gather(task, return_exceptions=True)
        raise
