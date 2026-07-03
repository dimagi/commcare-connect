from commcare_connect.ocs_provider.provider import OcsProvider


def test_provider_id_is_ocs():
    assert OcsProvider.id == "ocs"


def test_default_scope_requests_read_and_interact():
    # get_default_scope is an instance method; call it on a bare instance.
    provider = OcsProvider.__new__(OcsProvider)
    assert provider.get_default_scope() == ["openid", "chatbots:read", "chatbots:interact"]


def test_extract_uid_uses_sub():
    provider = OcsProvider.__new__(OcsProvider)
    assert provider.extract_uid({"sub": "abc-123", "email": "a@b.com"}) == "abc-123"


def test_pkce_is_enabled_with_s256_challenge():
    # OCS requires PKCE; allauth only emits a challenge when PKCE is enabled on the provider.
    provider = OcsProvider.__new__(OcsProvider)
    params = provider.get_pkce_params()
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"]
    assert params["code_verifier"]
