"""文本预览：超长时截断并补省略号。"""

ELLIPSIS = "..."


def truncate(text, limit=10):
    """把文本截断到 limit 个字符以内，超长时补省略号。"""
    if len(text.encode("utf-8")) > limit:
        return text[:limit] + ELLIPSIS
    return text
