package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

var ErrInvalidRequest = errors.New("invalid agent request")
var ErrModelConfigNotFound = errors.New("model config not found")
var ErrModelConfigInactive = errors.New("model config is inactive")

type Service struct {
	modelConfigRepo modelconfig.Repository
	runtimeClient   RuntimeClient
}

func NewService(modelConfigRepo modelconfig.Repository, runtimeClient RuntimeClient) *Service {
	return &Service{
		modelConfigRepo: modelConfigRepo,
		runtimeClient:   runtimeClient,
	}
}

func (s *Service) StreamText(
	ctx context.Context,
	user providercredential.UserContext,
	req StreamTextRequest,
) (TextStream, error) {
	if user.UserID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.modelConfigRepo == nil {
		return nil, fmt.Errorf("model config repository is not configured")
	}
	if s.runtimeClient == nil {
		return nil, fmt.Errorf("runtime client is not configured")
	}

	message := strings.TrimSpace(req.Message)
	if message == "" {
		return nil, fmt.Errorf("%w: message is required", ErrInvalidRequest)
	}

	config, err := s.resolveModelConfig(ctx, user.UserID, req.ModelConfigID)
	if err != nil {
		return nil, err
	}
	if config.Status != modelconfig.StatusActive {
		return nil, ErrModelConfigInactive
	}

	providerOptions := strings.TrimSpace(config.ProviderOptions)
	if providerOptions == "" {
		providerOptions = "{}"
	}

	return s.runtimeClient.StreamText(ctx, user, RuntimeStreamTextRequest{
		Provider:            config.Provider,
		Model:               config.ModelName,
		APIKey:              config.APIKey,
		BaseURL:             config.BaseURL,
		Prompt:              message,
		SystemPrompt:        strings.TrimSpace(req.SystemPrompt),
		Temperature:         config.Temperature,
		MaxTokens:           config.MaxTokens,
		ProviderOptionsJSON: providerOptions,
	})
}

func (s *Service) resolveModelConfig(ctx context.Context, userID int64, id int64) (*modelconfig.ModelConfig, error) {
	if id > 0 {
		config, err := s.modelConfigRepo.GetByID(ctx, userID, id)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return nil, ErrModelConfigNotFound
			}
			return nil, err
		}
		return config, nil
	}

	configs, err := s.modelConfigRepo.ListByUserID(ctx, userID)
	if err != nil {
		return nil, err
	}

	var firstActive *modelconfig.ModelConfig
	for _, config := range configs {
		if config == nil || config.Status != modelconfig.StatusActive {
			continue
		}
		if config.IsDefault {
			return config, nil
		}
		if firstActive == nil {
			firstActive = config
		}
	}
	if firstActive != nil {
		return firstActive, nil
	}

	return nil, ErrModelConfigNotFound
}
