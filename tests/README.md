# Tests

项目测试统一存放在这里：

- `api/`：Go API 测试，目录结构与 `apps/api/` 对应；
- `runtime/`：Python Runtime 的 pytest 测试；
- `web/`：Web 端的 Node 测试。

在仓库根目录运行 `make test` 可执行全部测试，也可以使用 `make test-go`、
`make test-runtime` 或 `make test-web` 分别执行。

Go 的同包测试需要访问包内未导出实现。`go-overlay.json` 会在执行
`make test-go` 时把 `tests/api/` 中的测试挂载到对应 Go 包，因此测试源码无需与
业务源码混放。`tests/api/go.mod` 只用于避免根模块把集中存放的测试误识别成独立包。
