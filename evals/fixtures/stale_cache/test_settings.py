from settings import Settings


def test_set_is_visible_to_get():
    """改完配置之后再读，应该拿到新值。"""
    settings = Settings({"timeout": 10})
    assert settings.get("timeout") == 10
    settings.set("timeout", 20)
    assert settings.get("timeout") == 20


def test_cache_still_works():
    """修法不能是把缓存删掉：同一个 key 只应该查底层一次。"""
    settings = Settings({"timeout": 10})
    settings.get("timeout")
    settings.get("timeout")
    assert settings.lookups == 1


def test_default_for_missing_key():
    settings = Settings()
    assert settings.get("nope", 5) == 5
