package pythonruntime

import (
	"agent-task-platform/apps/api/internal/config"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Dial creates the internal gRPC connection used by all runtime adapters.
// Transport security can be changed here without touching business modules.
func Dial(cfg config.PythonAgentConfig) (*grpc.ClientConn, error) {
	return grpc.NewClient(
		cfg.GRPCAddress(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
}
