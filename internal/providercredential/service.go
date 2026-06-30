package providercredential

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"agent-task-platform/pkg/validatex"
)

var ErrInvalidRequest = errors.New("invalid provider credential request")

type UserContext struct {
	UserID    int64
	Username  string
	SessionID string
	Device    string
}

type RuntimeClient interface {
	TestCredential(ctx context.Context, user UserContext, req TestCredentialRequest) (*TestCredentialResponse, error)
}

type Service struct {
	runtimeClient RuntimeClient
}

func NewService(runtimeClient RuntimeClient) *Service {
	return &Service{runtimeClient: runtimeClient}
}

func (s *Service) TestCredential(ctx context.Context, user UserContext, req TestCredentialRequest) (*TestCredentialResponse, error) {
	if user.UserID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}

	provider := strings.ToLower(strings.TrimSpace(req.Provider))
	apiKey := strings.TrimSpace(req.APIKey)
	baseURL := strings.TrimSpace(req.BaseURL)

	if provider == "" {
		return nil, fmt.Errorf("%w: provider is required", ErrInvalidRequest)
	}
	if apiKey == "" {
		return nil, fmt.Errorf("%w: api_key is required", ErrInvalidRequest)
	}

	if baseURL != "" {
		if err := validatex.HTTPURL(baseURL); err != nil {
			return nil, fmt.Errorf("%w: base_url %v", ErrInvalidRequest, err)
		}
	}

	return s.runtimeClient.TestCredential(ctx, user, TestCredentialRequest{
		Provider: provider,
		APIKey:   apiKey,
		BaseURL:  baseURL,
	})
}
