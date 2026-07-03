# proto 唯一真源在 api/proto/,生成物提交进 git:
#   Go     -> api/go/v1/provider/
#   Python -> python-agent/api/
# 依赖: protoc + protoc-gen-go + protoc-gen-go-grpc(Go 侧),
#       python-agent/.venv 内的 grpcio-tools(Python 侧,见 requirements-dev.txt)

PROTO_DIR := api/proto/v1/provider
GO_OUT    := api/go/v1/provider
PY_OUT    := python-agent/api
PY        := python-agent/.venv/bin/python

.PHONY: proto proto-go proto-python run-go run-python test-python

proto: proto-go proto-python

proto-go:
	rm -rf $(GO_OUT) && mkdir -p $(GO_OUT)
	protoc --proto_path=$(PROTO_DIR) \
	  --go_out=paths=source_relative:$(GO_OUT) \
	  --go-grpc_out=paths=source_relative:$(GO_OUT) \
	  $(PROTO_DIR)/provider.proto

proto-python:
	rm -f $(PY_OUT)/*_pb2*.py
	$(PY) -m grpc_tools.protoc --proto_path=$(PROTO_DIR) \
	  --python_out=$(PY_OUT) --grpc_python_out=$(PY_OUT) \
	  $(PROTO_DIR)/provider.proto
	perl -i -pe 's/^import (\w+_pb2)( as \w+)?$$/from . import $$1$$2/' $(PY_OUT)/provider_pb2_grpc.py

run-go:
	go run ./cmd/server --conf configs/config.yaml

run-python:
	cd python-agent && .venv/bin/python main.py --conf config.yaml

test-python:
	cd python-agent && .venv/bin/python -m pytest tests/
