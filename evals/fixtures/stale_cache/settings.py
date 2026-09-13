"""一个带缓存的小配置容器。"""


class Settings:
    """key-value 配置。带一层缓存，避免重复读底层数据。"""

    def __init__(self, values=None):
        self._values = dict(values or {})
        self._cache = {}
        self.lookups = 0  # 真实查询次数，用来观测缓存有没有生效

    def get(self, key, default=None):
        if key not in self._cache:
            self.lookups += 1
            self._cache[key] = self._values.get(key, default)
        return self._cache[key]

    def set(self, key, value):
        self._values[key] = value
