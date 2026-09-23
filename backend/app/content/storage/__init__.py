"""内容服务独立存储，不读取集成数据库。"""

from .store import ContentStore, StoreError

__all__ = ["ContentStore", "StoreError"]
