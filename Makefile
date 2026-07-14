# Shared protobuf source:
#   contracts/proto/        -> protocol source of truth
#   contracts/gen/go/       -> generated Go client
#   apps/runtime/api/       -> generated Python client

PROTO_DIR  := contracts/proto/v1/provider
GO_OUT     := contracts/gen/go/v1/provider
PY_OUT     := apps/runtime/api
RUNTIME_DIR := apps/runtime
WEB_DIR     := apps/web
TEST_DIR    := tests

# Override on Unix: make RUNTIME_PY=.venv/bin/python <target>
RUNTIME_PY ?= .venv/Scripts/python.exe

.PHONY: proto proto-go proto-python fmt run-api run-runtime run-web test test-go test-runtime test-web build-web

proto: proto-go proto-python

proto-go:
	rm -rf $(GO_OUT) && mkdir -p $(GO_OUT)
	cd $(RUNTIME_DIR) && $(RUNTIME_PY) -m grpc_tools.protoc \
	  --proto_path=../../$(PROTO_DIR) \
	  --go_out=paths=source_relative:../../$(GO_OUT) \
	  --go-grpc_out=paths=source_relative:../../$(GO_OUT) \
	  ../../$(PROTO_DIR)/provider.proto

proto-python:
	rm -f $(PY_OUT)/*_pb2*.py
	cd $(RUNTIME_DIR) && $(RUNTIME_PY) -m grpc_tools.protoc \
	  --proto_path=../../$(PROTO_DIR) \
	  --python_out=api --grpc_python_out=api \
	  ../../$(PROTO_DIR)/provider.proto
	perl -i -pe 's/^import (\w+_pb2)( as \w+)?$$/from . import $$1$$2/' $(PY_OUT)/provider_pb2_grpc.py

fmt:
	gofmt -w apps/api contracts/gen/go

run-api:
	go run ./apps/api/cmd/server --conf apps/api/config/config.yaml

run-runtime:
	cd $(RUNTIME_DIR) && $(RUNTIME_PY) main.py --conf config.yaml

run-web:
	npm --prefix $(WEB_DIR) run dev

test: test-go test-runtime test-web

test-go:
	go test -overlay=$(TEST_DIR)/go-overlay.json ./...

test-runtime:
	cd $(RUNTIME_DIR) && $(RUNTIME_PY) -m pytest ../../$(TEST_DIR)/runtime -q

test-web:
	npm --prefix $(WEB_DIR) test

build-web:
	npm --prefix $(WEB_DIR) run build
