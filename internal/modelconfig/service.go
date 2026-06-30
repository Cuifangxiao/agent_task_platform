package modelconfig

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"agent-task-platform/pkg/validatex"
)

var ErrInvalidRequest = errors.New("invalid model config request")

type Service struct {
	repo Repository
}

func NewService(repo Repository) *Service {
	return &Service{repo: repo}
}

func (s *Service) Create(ctx context.Context, userID int64, req CreateRequest) (*Response, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}

	name := strings.TrimSpace(req.Name)
	provider := strings.ToLower(strings.TrimSpace(req.Provider))
	modelName := strings.TrimSpace(req.ModelName)
	baseURL := strings.TrimSpace(req.BaseURL)
	apiKey := strings.TrimSpace(req.APIKey)

	if name == "" {
		return nil, fmt.Errorf("%w: name is required", ErrInvalidRequest)
	}
	if provider == "" {
		return nil, fmt.Errorf("%w: provider is required", ErrInvalidRequest)
	}
	if modelName == "" {
		return nil, fmt.Errorf("%w: model_name is required", ErrInvalidRequest)
	}
	if apiKey == "" {
		return nil, fmt.Errorf("%w: api_key is required", ErrInvalidRequest)
	}

	if baseURL != "" {
		if err := validatex.HTTPURL(baseURL); err != nil {
			return nil, fmt.Errorf("%w: base_url %v", ErrInvalidRequest, err)
		}
	}

	temperature := 1.0
	if req.Temperature != nil {
		temperature = *req.Temperature
	}
	if temperature < 0 || temperature > 2 {
		return nil, fmt.Errorf("%w: temperature must be between 0 and 2", ErrInvalidRequest)
	}

	maxTokens := 4096
	if req.MaxTokens != nil {
		maxTokens = *req.MaxTokens
	}
	if maxTokens <= 0 {
		return nil, fmt.Errorf("%w: max_tokens must be greater than 0", ErrInvalidRequest)
	}

	status := StatusActive
	if req.Status != "" {
		status = Status(strings.ToLower(strings.TrimSpace(req.Status)))
	}
	if status != StatusActive && status != StatusInactive {
		return nil, fmt.Errorf("%w: status must be active or inactive", ErrInvalidRequest)
	}

	created, err := s.repo.Create(ctx, ModelConfig{
		UserID:      userID,
		Name:        name,
		Provider:    provider,
		ModelName:   modelName,
		BaseURL:     baseURL,
		APIKey:      apiKey,
		Temperature: temperature,
		MaxTokens:   maxTokens,
		IsDefault:   req.IsDefault,
		Status:      status,
	})
	if err != nil {
		return nil, err
	}

	return toResponse(created), nil
}

func toResponse(config *ModelConfig) *Response {
	return &Response{
		ID:           config.ID,
		Name:         config.Name,
		Provider:     config.Provider,
		ModelName:    config.ModelName,
		BaseURL:      config.BaseURL,
		APIKeyMasked: maskAPIKey(config.APIKey),
		HasAPIKey:    config.APIKey != "",
		Temperature:  config.Temperature,
		MaxTokens:    config.MaxTokens,
		IsDefault:    config.IsDefault,
		Status:       string(config.Status),
		CreatedAt:    config.CreatedAt,
		UpdatedAt:    config.UpdatedAt,
	}
}

func maskAPIKey(apiKey string) string {
	apiKey = strings.TrimSpace(apiKey)
	if apiKey == "" {
		return ""
	}
	if len(apiKey) <= 4 {
		return strings.Repeat("*", len(apiKey))
	}

	return strings.Repeat("*", len(apiKey)-4) + apiKey[len(apiKey)-4:]
}
