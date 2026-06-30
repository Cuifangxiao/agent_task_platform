$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$proto = Join-Path $root "api\provider\v1\provider.proto"

protoc `
  --proto_path=$root `
  --go_out=$root `
  --go_opt=paths=source_relative `
  --go-grpc_out=$root `
  --go-grpc_opt=paths=source_relative `
  $proto

protoc `
  --proto_path=$root `
  --plugin=protoc-gen-grpc_python=E:\anaconda3\Library\bin\grpc_python_plugin.exe `
  --python_out=$root\python-agent\gen `
  --grpc_python_out=$root\python-agent\gen `
  $proto
