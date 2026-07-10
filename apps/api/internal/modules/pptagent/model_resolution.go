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

func (s *Service) resolveRuntimeModelConfigs(
	ctx context.Context,
	userID int64,
	agentConfigID int64,
) ([]RuntimeNodeModelConfig, error) {
	config, err := s.resolvePPTAgentConfig(ctx, userID, agentConfigID)
	if err != nil {
		return nil, err
	}
	return s.runtimeModelsFromConfigByValue(ctx, userID, config)
}

func (s *Service) runtimeModelsFromConfigByValue(
	ctx context.Context,
	userID int64,
	config *agentconfig.AgentConfig,
) ([]RuntimeNodeModelConfig, error) {
	if config == nil {
		return nil, fmt.Errorf("%w: agent config is required", ErrInvalidRequest)
	}
	agent, err := s.agentConfigRepo.GetAgentByID(ctx, config.AgentID)
	if err != nil {
		return nil, err
	}
	return s.runtimeModelsFromConfig(ctx, userID, config, agent)
}

func (s *Service) resolvePPTAgentConfig(
	ctx context.Context,
	userID int64,
	agentConfigID int64,
) (*agentconfig.AgentConfig, error) {
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
	if fallback != nil {
		return fallback, nil
	}

	return nil, ErrAgentConfigNotFound
}

func (s *Service) runtimeModelsFromConfig(
	ctx context.Context,
	userID int64,
	agentConfig *agentconfig.AgentConfig,
	agent *agentconfig.AgentDefinition,
) ([]RuntimeNodeModelConfig, error) {
	requiredNodes := modelNodeKeys(agent.Nodes)
	byNode := make(map[string]agentconfig.NodeBinding, len(agentConfig.NodeBindings))
	for _, binding := range agentConfig.NodeBindings {
		byNode[binding.NodeKey] = binding
	}

	runtimeModels := make([]RuntimeNodeModelConfig, 0, len(requiredNodes))
	for _, nodeKey := range requiredNodes {
		binding, ok := byNode[nodeKey]
		if !ok {
			fallbackKey := legacyModelNodeFallback(nodeKey)
			if fallbackKey != "" {
				binding, ok = byNode[fallbackKey]
			}
		}
		if !ok {
			return nil, fmt.Errorf("%w: agent config missing node binding '%s'", ErrInvalidRequest, nodeKey)
		}

		config, err := s.modelConfigRepo.GetByID(ctx, userID, binding.ModelConfigID)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return nil, fmt.Errorf("%w: model config for node '%s' was not found", ErrModelConfigNotFound, nodeKey)
			}
			return nil, err
		}
		if config.Status != modelconfig.StatusActive {
			return nil, fmt.Errorf("%w: model config for node '%s' is inactive", ErrModelConfigInactive, nodeKey)
		}

		temperature := config.Temperature
		if binding.Temperature != nil {
			temperature = *binding.Temperature
		}
		maxTokens := config.MaxTokens
		if binding.MaxTokens != nil {
			maxTokens = *binding.MaxTokens
		}

		runtimeModels = append(runtimeModels, RuntimeNodeModelConfig{
			NodeKey:             nodeKey,
			Provider:            config.Provider,
			Model:               config.ModelName,
			APIKey:              config.APIKey,
			BaseURL:             config.BaseURL,
			ProviderOptionsJSON: normalizedProviderOptions(config.ProviderOptions),
			Temperature:         temperature,
			MaxTokens:           maxTokens,
			SupportsImageInput:  supportsImageInput(config.ModelName, config.ProviderOptions),
		})
	}

	return runtimeModels, nil
}

func supportsImageInput(modelName string, providerOptions string) bool {
	var options map[string]any
	if json.Unmarshal([]byte(normalizedProviderOptions(providerOptions)), &options) == nil {
		if value, ok := options["supports_image_input"].(bool); ok {
			return value
		}
		if value, ok := options["supports_vision"].(bool); ok {
			return value
		}
	}
	name := strings.ToLower(modelName)
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

func legacyModelNodeFallback(nodeKey string) string {
	switch strings.ToLower(strings.TrimSpace(nodeKey)) {
	case "brief":
		return "outline"
	case "style", "writer", "designer", "coherence":
		return "slides"
	case "repair":
		return "designer"
	default:
		return ""
	}
}

func modelNodeKeys(nodes []agentconfig.NodeDefinition) []string {
	keys := make([]string, 0, len(nodes))
	for _, node := range nodes {
		if node.RequiresModel {
			keys = append(keys, node.Key)
		}
	}
	return keys
}

func normalizedProviderOptions(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "{}"
	}
	return value
}
