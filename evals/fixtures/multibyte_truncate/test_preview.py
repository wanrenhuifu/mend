from preview import truncate


def test_short_chinese_text_is_not_truncated():
    """六个汉字不该被当成"超过十个字符"。"""
    assert truncate("你好世界和平", limit=10) == "你好世界和平"


def test_long_text_gets_ellipsis():
    assert truncate("abcdefghijklmnop", limit=10) == "abcdefghij" + "..."


def test_exactly_at_limit_is_untouched():
    assert truncate("abcdefghij", limit=10) == "abcdefghij"
