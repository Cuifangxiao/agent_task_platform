from api import provider_pb2
from core.provider.service import ProviderService
from handler.provider_handler import ProviderHandler


def test_test_credential_maps_proto_both_ways():
    handler = ProviderHandler(ProviderService())

    req = provider_pb2.TestCredentialRequest(
        user=provider_pb2.UserContext(user_id=1, username="alice"),
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
    )

    resp = handler.test_credential(req)

    assert isinstance(resp, provider_pb2.TestCredentialResponse)
    assert resp.provider == "openai"
    assert resp.status == "received"
    assert resp.request_id
