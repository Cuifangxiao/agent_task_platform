package modelconfig

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"agent-task-platform/pkg/validatex"
)

var ErrInvalidRequest = errors.New("invalid model config request")
var ErrNotFound = errors.New("model config not found")

type Service struct {
	repo Repository
}

func NewService(repo Repository) *Service {
	return &Service{repo: repo}
}

func (s *Service) Create(ctx context.Context, userID int64, req CreateRequest) (*Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}

	config, err := normalizeInput(modelConfigInput{
		Name:        req.Name,
		Provider:    req.Provider,
		ModelName:   req.ModelName,
		BaseURL:     req.BaseURL,
		APIKey:      req.APIKey,
		Temperature: req.Temperature,
		MaxTokens:   req.MaxTokens,
		IsDefault:   req.IsDefault,
		Status:      req.Status,
	})
	if err != nil {
		return nil, err
	}
	config.UserID = userID

	created, err := s.repo.Create(ctx, config)
	if err != nil {
		return nil, err
	}

	return toResponse(created), nil
}

func (s *Service) List(ctx context.Context, userID int64) ([]Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}

	configs, err := s.repo.ListByUserID(ctx, userID)
	if err != nil {
		return nil, err
	}

	return toResponses(configs), nil
}

func (s *Service) Get(ctx context.Context, userID int64, id int64) (*Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}
	if err := validateModelConfigID(id); err != nil {
		return nil, err
	}

	config, err := s.repo.GetByID(ctx, userID, id)
	if err != nil {
		return nil, err
	}

	return toResponse(config), nil
}

func (s *Service) Update(ctx context.Context, userID int64, id int64, req UpdateRequest) (*Response, error) {
	if err := validateUserID(userID); err != nil {
		return nil, err
	}
	if err := validateModelConfigID(id); err != nil {
		return nil, err
	}

	existing, err := s.repo.GetByID(ctx, userID, id)
	if err != nil {
		return nil, err
	}

	input := modelConfigInput{
		Name:        existing.Name,
		Provider:    existing.Provider,
		ModelName:   existing.ModelName,
		BaseURL:     existing.BaseURL,
		APIKey:      existing.APIKey,
		Temperature: &existing.Temperature,
		MaxTokens:   &existing.MaxTokens,
		IsDefault:   existing.IsDefault,
		Status:      string(existing.Status),
	}

	if req.Name != nil {
		input.Name = *req.Name
	}
	if req.Provider != nil {
		input.Provider = *req.Provider
	}
	if req.ModelName != nil {
		input.ModelName = *req.ModelName
	}
	if req.BaseURL != nil {
		input.BaseURL = *req.BaseURL
	}
	if req.APIKey != nil {
		input.APIKey = *req.APIKey
	}
	if req.Temperature != nil {
		input.Temperature = req.Temperature
	}
	if req.MaxTokens != nil {
		input.MaxTokens = req.MaxTokens
	}
	if req.IsDefault != nil {
		input.IsDefault = *req.IsDefault
	}
	if req.Status != nil {
		input.Status = *req.Status
	}

	config, err := normalizeInput(input)
	if err != nil {
		return nil, err
	}
	config.ID = id
	config.UserID = userID

	updated, err := s.repo.Update(ctx, config)
	if err != nil {
		return nil, err
	}

	return toResponse(updated), nil
}

func (s *Service) Delete(ctx context.Context, userID int64, id int64) error {
	if err := validateUserID(userID); err != nil {
		return err
	}
	if err := validateModelConfigID(id); err != nil {
		return err
	}

	return s.repo.Delete(ctx, userID, id)
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

func toResponses(configs []*ModelConfig) []Response {
	responses := make([]Response, 0, len(configs))
	for _, config := range configs {
		if config == nil {
			continue
		}
		responses = append(responses, *toResponse(config))
	}

	return responses
}

type modelConfigInput struct {
	Name        string
	Provider    string
	ModelName   string
	BaseURL     string
	APIKey      string
	Temperature *float64
	MaxTokens   *int
	IsDefault   bool
	Status      string
}

func normalizeInput(input modelConfigInput) (ModelConfig, error) {
	name := strings.TrimSpace(input.Name)
	provider := strings.ToLower(strings.TrimSpace(input.Provider))
	modelName := strings.TrimSpace(input.ModelName)
	baseURL := strings.TrimSpace(input.BaseURL)
	apiKey := strings.TrimSpace(input.APIKey)

	if name == "" {
		return ModelConfig{}, fmt.Errorf("%w: name is required", ErrInvalidRequest)
	}
	if provider == "" {
		return ModelConfig{}, fmt.Errorf("%w: provider is required", ErrInvalidRequest)
	}
	if modelName == "" {
		return ModelConfig{}, fmt.Errorf("%w: model_name is required", ErrInvalidRequest)
	}
	if apiKey == "" {
		return ModelConfig{}, fmt.Errorf("%w: api_key is required", ErrInvalidRequest)
	}

	if baseURL != "" {
		if err := validatex.HTTPURL(baseURL); err != nil {
			return ModelConfig{}, fmt.Errorf("%w: base_url %v", ErrInvalidRequest, err)
		}
	}

	temperature := 1.0
	if input.Temperature != nil {
		temperature = *input.Temperature
	}
	if temperature < 0 || temperature > 2 {
		return ModelConfig{}, fmt.Errorf("%w: temperature must be between 0 and 2", ErrInvalidRequest)
	}

	maxTokens := 4096
	if input.MaxTokens != nil {
		maxTokens = *input.MaxTokens
	}
	if maxTokens <= 0 {
		return ModelConfig{}, fmt.Errorf("%w: max_tokens must be greater than 0", ErrInvalidRequest)
	}

	status := StatusActive
	if input.Status != "" {
		status = Status(strings.ToLower(strings.TrimSpace(input.Status)))
	}
	if status != StatusActive && status != StatusInactive {
		return ModelConfig{}, fmt.Errorf("%w: status must be active or inactive", ErrInvalidRequest)
	}

	return ModelConfig{
		Name:        name,
		Provider:    provider,
		ModelName:   modelName,
		BaseURL:     baseURL,
		APIKey:      apiKey,
		Temperature: temperature,
		MaxTokens:   maxTokens,
		IsDefault:   input.IsDefault,
		Status:      status,
	}, nil
}

func validateUserID(userID int64) error {
	if userID <= 0 {
		return fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}

	return nil
}

func validateModelConfigID(id int64) error {
	if id <= 0 {
		return fmt.Errorf("%w: invalid model config id", ErrInvalidRequest)
	}

	return nil
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
