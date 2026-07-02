from commcare_connect.ocs_provider.provider import OcsProvider


def test_provider_id_is_ocs():
    assert OcsProvider.id == "ocs"


def test_default_scope_requests_read_and_interact():
    # get_default_scope is an instance method; call it on a bare instance.
    provider = OcsProvider.__new__(OcsProvider)
    assert provider.get_default_scope() == ["chatbots:read", "chatbots:interact"]


def test_extract_uid_uses_sub():
    provider = OcsProvider.__new__(OcsProvider)
    assert provider.extract_uid({"sub": "abc-123", "email": "a@b.com"}) == "abc-123"
