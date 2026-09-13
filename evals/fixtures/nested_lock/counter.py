"""一个线程安全的计数器。"""

import threading


class Counter:
    def __init__(self):
        self._lock = threading.Lock()
        self._value = 0

    @property
    def value(self):
        return self._value

    def add(self, amount):
        with self._lock:
            self._value += amount

    def add_twice(self, amount):
        """加两次，整个过程应该是原子的。"""
        with self._lock:
            self.add(amount)
            self.add(amount)
