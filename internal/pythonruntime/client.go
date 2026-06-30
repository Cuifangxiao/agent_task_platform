package pythonruntime

import (
	"context"
	"fmt"
	"time"

	providerv1 "agent-task-platform/api/provider/v1"
	"agent-task-platform/internal/providercredential"
	"google.golang.org/grpc"
)

type Client struct {
	grpcClient providerv1.ProviderRuntimeServiceClient
	timeout    time.Duration
}

func NewClient(conn grpc.ClientConnInterface) *Client {
	return &Client{
		grpcClient: providerv1.NewProviderRuntimeServiceClient(conn),
		timeout:    5 * time.Second,
	}
}

func (c *Client) TestCredential(
	ctx context.Context,
	user providercredential.UserContext,
	req providercredential.TestCredentialRequest,
) (*providercredential.TestCredentialResponse, error) {
	callCtx, cancel := context.WithTimeout(ctx, c.timeout)
	defer cancel()

	resp, err := c.grpcClient.TestCredential(callCtx, &providerv1.TestCredentialRequest{
		User: &providerv1.UserContext{
			UserId:    user.UserID,
			Username:  user.Username,
			SessionId: user.SessionID,
			Device:    user.Device,
		},
		Provider: req.Provider,
		ApiKey:   req.APIKey,
		BaseUrl:  req.BaseURL,
	})
	if err != nil {
		return nil, fmt.Errorf("call python runtime test credential failed: %w", err)
	}

	return &providercredential.TestCredentialResponse{
		RequestID: resp.GetRequestId(),
		Provider:  resp.GetProvider(),
		Status:    resp.GetStatus(),
		Message:   resp.GetMessage(),
	}, nil
}
