from control.api_keys import ApiKeyService


def test_api_key_service_hash_and_verify_roundtrip() -> None:
    service = ApiKeyService(secret="test-secret")
    key = service.generate_key(prefix="wk")
    hashed = service.hash_key(key)

    assert key.startswith("wk_")
    assert hashed != key
    assert service.verify_key(key, hashed) is True
    assert service.verify_key("wk_invalid", hashed) is False


def test_api_key_service_extract_key_id() -> None:
    service = ApiKeyService(secret="test-secret")
    key = service.generate_key(prefix="wk")
    key_id = service.key_id(key)
    assert key_id
