from app.services.provider_service import ProviderService


def test_test_credential_returns_received():
    service = ProviderService()
    result = service.test_credential(
        user_id=1,
        username="alice",
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
    )

    assert result.provider == "openai"
    assert result.status == "received"
    assert result.request_id
