import threading

from counter import Counter


def test_add_twice_does_not_deadlock():
    """add_twice 现在会卡住。这里用带超时的线程跑，免得测试本身把整个进程挂死。"""
    counter = Counter()
    thread = threading.Thread(target=counter.add_twice, args=(3,), daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive(), "add_twice 卡住了（一直没返回）"
    assert counter.value == 6


def test_add_accumulates():
    counter = Counter()
    counter.add(2)
    counter.add(5)
    assert counter.value == 7
