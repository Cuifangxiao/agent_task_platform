package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
)

var pptModelNodes = [...]string{"outline", "style", "designer"}

func (s *Service) resolveRuntimeModelConfigs(ctx context.Context, userID, agentConfigID int64) ([]RuntimeModelConfig, int64, error) {
	config, err := s.resolvePPTAgentConfig(ctx, userID, agentConfigID)
	if err != nil {
		return nil, 0, err
	}
	bindings := make(map[string]agentconfig.NodeBinding, len(config.NodeBindings))
	for _, binding := range config.NodeBindings {
		bindings[strings.ToLower(strings.TrimSpace(binding.NodeKey))] = binding
	}

	models := make([]RuntimeModelConfig, 0, len(pptModelNodes))
	for _, nodeKey := range pptModelNodes {
		binding, ok := bindings[nodeKey]
		if !ok {
			return nil, 0, fmt.Errorf("%w: agent config missing node binding %q", ErrInvalidRequest, nodeKey)
		}
		config, err := s.modelConfigRepo.GetByID(ctx, userID, binding.ModelConfigID)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return nil, 0, fmt.Errorf("%w: model for node %q", ErrModelConfigNotFound, nodeKey)
			}
			return nil, 0, err
		}
		if config.Status != modelconfig.StatusActive {
			return nil, 0, fmt.Errorf("%w: node %q", ErrModelConfigInactive, nodeKey)
		}
		if config.ModelType == modelconfig.ModelTypeImage {
			return nil, 0, fmt.Errorf("%w: node %q requires a text or multimodal model", ErrInvalidRequest, nodeKey)
		}

		temperature := config.Temperature
		if binding.Temperature != nil {
			temperature = *binding.Temperature
		}
		maxTokens := config.MaxTokens
		if binding.MaxTokens != nil {
			maxTokens = *binding.MaxTokens
		}
		if maxTokens <= 0 {
			return nil, 0, fmt.Errorf("%w: max tokens for node %q must be positive", ErrInvalidRequest, nodeKey)
		}
		options, err := decodeProviderOptions(config.ProviderOptions)
		if err != nil {
			return nil, 0, fmt.Errorf("%w: provider options for node %q: %v", ErrInvalidRequest, nodeKey, err)
		}
		models = append(models, RuntimeModelConfig{
			NodeKey:            nodeKey,
			Provider:           config.Provider,
			Model:              config.ModelName,
			APIKey:             config.APIKey,
			BaseURL:            config.BaseURL,
			ProviderOptions:    options,
			Temperature:        temperature,
			MaxTokens:          maxTokens,
			SupportsImageInput: modelConfigSupportsImageInput(config, options),
		})
	}
	return models, config.ID, nil
}

func (s *Service) resolvePPTAgentConfig(ctx context.Context, userID, agentConfigID int64) (*agentconfig.AgentConfig, error) {
	if agentConfigID > 0 {
		config, err := s.agentConfigRepo.GetConfigByID(ctx, userID, agentConfigID)
		if err != nil {
			if errors.Is(err, agentconfig.ErrNotFound) {
				return nil, ErrAgentConfigNotFound
			}
			return nil, err
		}
		if config.AgentKey != agentconfig.AgentKeyPPT {
			return nil, fmt.Errorf("%w: agent config is not for ppt_agent", ErrInvalidRequest)
		}
		if config.Status != agentconfig.StatusActive {
			return nil, fmt.Errorf("%w: agent config is inactive", ErrInvalidRequest)
		}
		return config, nil
	}

	configs, err := s.agentConfigRepo.ListConfigsByUserID(ctx, userID, 0)
	if err != nil {
		return nil, err
	}
	var fallback *agentconfig.AgentConfig
	for _, config := range configs {
		if config == nil || config.AgentKey != agentconfig.AgentKeyPPT || config.Status != agentconfig.StatusActive {
			continue
		}
		if config.IsDefault {
			return config, nil
		}
		if fallback == nil {
			fallback = config
		}
	}
	if fallback == nil {
		return nil, ErrAgentConfigNotFound
	}
	return fallback, nil
}

func decodeProviderOptions(raw string) (map[string]any, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return map[string]any{}, nil
	}
	var options map[string]any
	if err := json.Unmarshal([]byte(raw), &options); err != nil {
		return nil, err
	}
	if options == nil {
		options = map[string]any{}
	}
	return options, nil
}

func modelConfigSupportsImageInput(config *modelconfig.ModelConfig, options map[string]any) bool {
	if value, ok := options["supports_image_input"].(bool); ok {
		return value
	}
	if value, ok := options["supports_vision"].(bool); ok {
		return value
	}
	if config == nil || config.ModelType == modelconfig.ModelTypeImage {
		return false
	}
	if config.ModelType == modelconfig.ModelTypeMultimodal {
		return true
	}
	name := strings.ToLower(config.ModelName)
	if strings.Contains(name, "qwen") && (strings.Contains(name, "-vl") || strings.Contains(name, "vl-")) {
		return true
	}
	for _, marker := range []string{"gpt-4o", "gpt-4.1", "gpt-5", "vision", "gemini", "claude-3", "claude-4"} {
		if strings.Contains(name, marker) {
			return true
		}
	}
	return false
}
