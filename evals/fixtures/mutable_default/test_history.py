from history import add_event


def test_second_call_starts_clean():
    """不传 history 时，每次调用都应该是新的一条记录。"""
    add_event("first")
    assert add_event("second") == ["second"]


def test_can_continue_an_existing_history():
    """传了 history 就要在它后面继续追加（这条锁住语义，防止用"总是返回单元素列表"糊过去）。"""
    assert add_event("b", history=["a"]) == ["a", "b"]
