package pptagent

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

var ErrInvalidRequest = errors.New("invalid ppt agent request")
var ErrAgentConfigNotFound = errors.New("agent config not found")
var ErrModelConfigNotFound = errors.New("model config not found")
var ErrModelConfigInactive = errors.New("model config is inactive")
var ErrArtifactNotFound = errors.New("deck artifact not found")

var artifactIDPattern = regexp.MustCompile(`^[a-fA-F0-9-]{36}$`)

type Service struct {
	modelConfigRepo modelconfig.Repository
	agentConfigRepo agentconfig.Repository
	runtimeClient   RuntimeClient
	artifactDir     string
}

func NewService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	runtimeClient RuntimeClient,
	artifactDir string,
) *Service {
	if strings.TrimSpace(artifactDir) == "" {
		artifactDir = filepath.Join("python-agent", "artifacts", "decks")
	}
	if abs, err := filepath.Abs(artifactDir); err == nil {
		artifactDir = abs
	}

	return &Service{
		modelConfigRepo: modelConfigRepo,
		agentConfigRepo: agentConfigRepo,
		runtimeClient:   runtimeClient,
		artifactDir:     artifactDir,
	}
}

func (s *Service) GenerateDeck(
	ctx context.Context,
	user providercredential.UserContext,
	req GenerateDeckRequest,
) (DeckStream, error) {
	if user.UserID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.modelConfigRepo == nil {
		return nil, fmt.Errorf("model config repository is not configured")
	}
	if s.agentConfigRepo == nil {
		return nil, fmt.Errorf("agent config repository is not configured")
	}
	if s.runtimeClient == nil {
		return nil, fmt.Errorf("runtime client is not configured")
	}

	description := strings.TrimSpace(req.Description)
	if description == "" {
		return nil, fmt.Errorf("%w: description is required", ErrInvalidRequest)
	}
	if len([]rune(description)) > 5000 {
		return nil, fmt.Errorf("%w: description must be <= 5000 characters", ErrInvalidRequest)
	}
	if len([]rune(req.Audience)) > 500 {
		return nil, fmt.Errorf("%w: audience must be <= 500 characters", ErrInvalidRequest)
	}
	if len([]rune(req.Goal)) > 500 {
		return nil, fmt.Errorf("%w: goal must be <= 500 characters", ErrInvalidRequest)
	}
	if len([]rune(req.Language)) > 64 {
		return nil, fmt.Errorf("%w: language must be <= 64 characters", ErrInvalidRequest)
	}
	if len([]rune(req.Style)) > 200 {
		return nil, fmt.Errorf("%w: style must be <= 200 characters", ErrInvalidRequest)
	}
	if len([]rune(req.SourceText)) > 50000 {
		return nil, fmt.Errorf("%w: source_text must be <= 50000 characters", ErrInvalidRequest)
	}

	runtimeModels, err := s.resolveRuntimeModelConfigs(ctx, user.UserID, req.AgentConfigID)
	if err != nil {
		return nil, err
	}

	return s.runtimeClient.GenerateDeck(ctx, user, RuntimeGenerateDeckRequest{
		Description:      description,
		Audience:         defaultString(req.Audience, "general audience"),
		Goal:             defaultString(req.Goal, "create a presentation that follows the user description"),
		PageCount:        clampPageCount(req.PageCount),
		Language:         defaultString(req.Language, "zh-CN"),
		Style:            defaultString(req.Style, "concise business deck"),
		SourceText:       strings.TrimSpace(req.SourceText),
		SourcePDFName:    strings.TrimSpace(req.SourcePDFName),
		SourcePDFBytes:   req.SourcePDFBytes,
		NodeModelConfigs: runtimeModels,
	})
}

func (s *Service) ArtifactPath(requestID string) (string, error) {
	requestID = strings.TrimSpace(requestID)
	if !artifactIDPattern.MatchString(requestID) {
		return "", fmt.Errorf("%w: invalid request id", ErrInvalidRequest)
	}

	path := filepath.Join(s.artifactDir, requestID+".pptx")
	cleanDir := filepath.Clean(s.artifactDir)
	cleanPath := filepath.Clean(path)
	rel, err := filepath.Rel(cleanDir, cleanPath)
	if err != nil || strings.HasPrefix(rel, "..") || filepath.IsAbs(rel) {
		return "", fmt.Errorf("%w: invalid artifact path", ErrInvalidRequest)
	}

	info, err := os.Stat(cleanPath)
	if err != nil {
		if os.IsNotExist(err) {
			return "", ErrArtifactNotFound
		}
		return "", err
	}
	if info.IsDir() {
		return "", ErrArtifactNotFound
	}

	return cleanPath, nil
}

func (s *Service) resolveRuntimeModelConfigs(
	ctx context.Context,
	userID int64,
	agentConfigID int64,
) ([]RuntimeNodeModelConfig, error) {
	if agentConfigID <= 0 {
		return nil, fmt.Errorf("%w: agent_config_id is required", ErrInvalidRequest)
	}

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

	agent, err := s.agentConfigRepo.GetAgentByID(ctx, config.AgentID)
	if err != nil {
		return nil, err
	}
	return s.runtimeModelsFromConfig(ctx, userID, config, agent)
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
		})
	}

	return runtimeModels, nil
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

func defaultString(value string, fallback string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback
	}
	return value
}

func normalizedProviderOptions(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "{}"
	}
	return value
}

func clampPageCount(value int) int {
	if value <= 0 {
		return 8
	}
	if value > 30 {
		return 30
	}
	return value
}
