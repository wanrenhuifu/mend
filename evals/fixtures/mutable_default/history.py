"""事件记录。这个模块留了一个经典的 Python 陷阱：可变默认参数在调用之间共享。"""


def add_event(event, history=[]):
    """把事件追加到记录里，返回更新后的记录。

    history 可以传入已有的记录继续追加；不传时应该是一条全新的记录。
    """
    history.append(event)
    return history
