import pytest
from pydantic import ValidationError

from config import Config, init_config


def test_init_config_from_example_yaml():
    cfg = init_config("config.example.yaml")

    assert cfg.name == "python-agent"
    assert cfg.server.grpc.addr == "0.0.0.0:50051"
    assert cfg.server.grpc.max_workers == 10


def test_invalid_port_rejected():
    with pytest.raises(ValidationError):
        Config(server={"grpc": {"port": 70000}})


def test_missing_file_raises_value_error():
    with pytest.raises(ValueError):
        init_config("no-such-config.yaml")
