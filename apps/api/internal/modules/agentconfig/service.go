package agentconfig

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"agent-task-platform/apps/api/internal/modules/modelconfig"
)

var ErrInvalidRequest = errors.New("invalid agent config request")
var ErrNotFound = errors.New("agent config not found")

type ModelConfigRepository interface {
	GetByID(ctx context.Context, userID int64, id int64) (*modelconfig.ModelConfig, error)
}

type Service struct {
	repo            Repository
	modelConfigRepo ModelConfigRepository
}

func NewService(repo Repository, modelConfigRepo ModelConfigRepository) *Service {
	return &Service{
		repo:            repo,
		modelConfigRepo: modelConfigRepo,
	}
}

func (s *Service) ListAgents(ctx context.Context) ([]AgentResponse, error) {
	agents, err := s.repo.ListAgents(ctx)
	if err != nil {
		return nil, err
	}

	return toAgentResponses(agents), nil
}

func (s *Service) GetAgent(ctx context.Context, id int64) (*AgentResponse, error) {
	if id <= 0 {
		return nil, fmt.Errorf("%w: invalid agent id", ErrInvalidRequest)
	}

	agent, err := s.repo.GetAgentByID(ctx, id)
	if err != nil {
		return nil, err
	}

	return toAgentResponse(agent), nil
}

func (s *Service) CreateConfig(ctx context.Context, user actor.User, req CreateConfigRequest) (*ConfigResponse, error) {
	if err := validateUser(user); err != nil {
		return nil, err
	}

	agent, err := s.repo.GetAgentByID(ctx, req.AgentID)
	if err != nil {
		return nil, err
	}
	if agent.Status != StatusActive {
		return nil, fmt.Errorf("%w: agent is inactive", ErrInvalidRequest)
	}

	config, err := normalizeCreateInput(req)
	if err != nil {
		return nil, err
	}
	if err := s.validateNodeBindings(ctx, user.UserID, agent, config.NodeBindings); err != nil {
		return nil, err
	}

	config.UserID = user.UserID
	created, err := s.repo.CreateConfig(ctx, config)
	if err != nil {
		return nil, err
	}

	return toConfigResponse(created), nil
}

func (s *Service) ListConfigs(ctx context.Context, userID int64, agentID int64) ([]ConfigResponse, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if agentID < 0 {
		return nil, fmt.Errorf("%w: invalid agent id", ErrInvalidRequest)
	}

	configs, err := s.repo.ListConfigsByUserID(ctx, userID, agentID)
	if err != nil {
		return nil, err
	}

	return toConfigResponses(configs), nil
}

func (s *Service) GetConfig(ctx context.Context, userID int64, id int64) (*ConfigResponse, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if id <= 0 {
		return nil, fmt.Errorf("%w: invalid config id", ErrInvalidRequest)
	}

	config, err := s.repo.GetConfigByID(ctx, userID, id)
	if err != nil {
		return nil, err
	}

	return toConfigResponse(config), nil
}

func (s *Service) UpdateConfig(ctx context.Context, user actor.User, id int64, req UpdateConfigRequest) (*ConfigResponse, error) {
	if err := validateUser(user); err != nil {
		return nil, err
	}
	if id <= 0 {
		return nil, fmt.Errorf("%w: invalid config id", ErrInvalidRequest)
	}

	existing, err := s.repo.GetConfigByID(ctx, user.UserID, id)
	if err != nil {
		return nil, err
	}
	agent, err := s.repo.GetAgentByID(ctx, existing.AgentID)
	if err != nil {
		return nil, err
	}

	updated, err := normalizeUpdateInput(*existing, req)
	if err != nil {
		return nil, err
	}
	if err := s.validateNodeBindings(ctx, user.UserID, agent, updated.NodeBindings); err != nil {
		return nil, err
	}

	saved, err := s.repo.UpdateConfig(ctx, updated)
	if err != nil {
		return nil, err
	}

	return toConfigResponse(saved), nil
}

func (s *Service) DeleteConfig(ctx context.Context, userID int64, id int64) error {
	if userID <= 0 {
		return fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if id <= 0 {
		return fmt.Errorf("%w: invalid config id", ErrInvalidRequest)
	}

	return s.repo.DeleteConfig(ctx, userID, id)
}

func (s *Service) validateNodeBindings(
	ctx context.Context,
	userID int64,
	agent *AgentDefinition,
	bindings []NodeBinding,
) error {
	if s.modelConfigRepo == nil {
		return fmt.Errorf("model config repository is not configured")
	}
	if agent == nil {
		return fmt.Errorf("%w: agent is required", ErrInvalidRequest)
	}

	requiredNodes := modelNodeKeys(agent.Nodes)
	seen := make(map[string]struct{}, len(bindings))
	for _, binding := range bindings {
		if binding.NodeKey == "" {
			return fmt.Errorf("%w: node_key is required", ErrInvalidRequest)
		}
		if _, ok := seen[binding.NodeKey]; ok {
			return fmt.Errorf("%w: duplicate node_key '%s'", ErrInvalidRequest, binding.NodeKey)
		}
		seen[binding.NodeKey] = struct{}{}

		node := nodeDefinition(agent.Nodes, binding.NodeKey)
		if node == nil || !node.RequiresModel {
			return fmt.Errorf("%w: node_key '%s' is not configurable for agent '%s'", ErrInvalidRequest, binding.NodeKey, agent.AgentKey)
		}
		if binding.ModelConfigID <= 0 {
			return fmt.Errorf("%w: model_config_id is required for node '%s'", ErrInvalidRequest, binding.NodeKey)
		}
		if binding.Temperature != nil && (*binding.Temperature < 0 || *binding.Temperature > 2) {
			return fmt.Errorf("%w: temperature for node '%s' must be between 0 and 2", ErrInvalidRequest, binding.NodeKey)
		}
		if binding.MaxTokens != nil && *binding.MaxTokens <= 0 {
			return fmt.Errorf("%w: max_tokens for node '%s' must be positive", ErrInvalidRequest, binding.NodeKey)
		}

		config, err := s.modelConfigRepo.GetByID(ctx, userID, binding.ModelConfigID)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return fmt.Errorf("%w: model_config_id %d for node '%s' was not found", ErrInvalidRequest, binding.ModelConfigID, binding.NodeKey)
			}
			return err
		}
		if config.Status != modelconfig.StatusActive {
			return fmt.Errorf("%w: model_config_id %d for node '%s' is not active", ErrInvalidRequest, binding.ModelConfigID, binding.NodeKey)
		}
		if config.ModelType == modelconfig.ModelTypeImage {
			return fmt.Errorf("%w: model_config_id %d for node '%s' is an image model", ErrInvalidRequest, binding.ModelConfigID, binding.NodeKey)
		}
	}

	for _, nodeKey := range requiredNodes {
		if _, ok := seen[nodeKey]; !ok {
			return fmt.Errorf("%w: missing binding for node '%s'", ErrInvalidRequest, nodeKey)
		}
	}

	return nil
}

func normalizeCreateInput(req CreateConfigRequest) (AgentConfig, error) {
	status, err := normalizeStatus(req.Status)
	if err != nil {
		return AgentConfig{}, err
	}
	if req.AgentID <= 0 {
		return AgentConfig{}, fmt.Errorf("%w: agent_id is required", ErrInvalidRequest)
	}
	if req.IsDefault && status != StatusActive {
		return AgentConfig{}, fmt.Errorf("%w: default config must be active", ErrInvalidRequest)
	}

	name := strings.TrimSpace(req.Name)
	if name == "" {
		return AgentConfig{}, fmt.Errorf("%w: name is required", ErrInvalidRequest)
	}

	bindings, err := normalizeBindingRequests(req.NodeBindings)
	if err != nil {
		return AgentConfig{}, err
	}

	return AgentConfig{
		AgentID:      req.AgentID,
		Name:         name,
		Description:  strings.TrimSpace(req.Description),
		IsDefault:    req.IsDefault,
		Status:       status,
		NodeBindings: bindings,
	}, nil
}

func normalizeUpdateInput(existing AgentConfig, req UpdateConfigRequest) (AgentConfig, error) {
	updated := existing
	if req.Name != nil {
		updated.Name = strings.TrimSpace(*req.Name)
		if updated.Name == "" {
			return AgentConfig{}, fmt.Errorf("%w: name is required", ErrInvalidRequest)
		}
	}
	if req.Description != nil {
		updated.Description = strings.TrimSpace(*req.Description)
	}
	if req.IsDefault != nil {
		updated.IsDefault = *req.IsDefault
	}
	if req.Status != nil {
		status, err := normalizeStatus(*req.Status)
		if err != nil {
			return AgentConfig{}, err
		}
		updated.Status = status
	}
	if updated.IsDefault && updated.Status != StatusActive {
		return AgentConfig{}, fmt.Errorf("%w: default config must be active", ErrInvalidRequest)
	}
	if req.NodeBindings != nil {
		bindings, err := normalizeBindingRequests(*req.NodeBindings)
		if err != nil {
			return AgentConfig{}, err
		}
		updated.NodeBindings = bindings
	}

	return updated, nil
}

func normalizeBindingRequests(requests []NodeBindingRequest) ([]NodeBinding, error) {
	if len(requests) == 0 {
		return nil, fmt.Errorf("%w: node_bindings is required", ErrInvalidRequest)
	}

	bindings := make([]NodeBinding, 0, len(requests))
	for _, req := range requests {
		nodeKey := strings.ToLower(strings.TrimSpace(req.NodeKey))
		if nodeKey == "" {
			return nil, fmt.Errorf("%w: node_key is required", ErrInvalidRequest)
		}

		bindings = append(bindings, NodeBinding{
			NodeKey:       nodeKey,
			ModelConfigID: req.ModelConfigID,
			Temperature:   req.Temperature,
			MaxTokens:     req.MaxTokens,
		})
	}

	return bindings, nil
}

func normalizeStatus(value string) (Status, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", string(StatusActive):
		return StatusActive, nil
	case string(StatusInactive):
		return StatusInactive, nil
	default:
		return "", fmt.Errorf("%w: status must be active or inactive", ErrInvalidRequest)
	}
}

func validateUser(user actor.User) error {
	if user.UserID <= 0 {
		return fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	return nil
}

func modelNodeKeys(nodes []NodeDefinition) []string {
	keys := make([]string, 0, len(nodes))
	for _, node := range nodes {
		if node.RequiresModel {
			keys = append(keys, node.Key)
		}
	}
	return keys
}

func nodeDefinition(nodes []NodeDefinition, nodeKey string) *NodeDefinition {
	nodeKey = strings.ToLower(strings.TrimSpace(nodeKey))
	for _, node := range nodes {
		if strings.EqualFold(node.Key, nodeKey) {
			current := node
			return &current
		}
	}
	return nil
}

func toAgentResponse(agent *AgentDefinition) *AgentResponse {
	if agent == nil {
		return nil
	}

	nodes := make([]NodeDefinitionResponse, 0, len(agent.Nodes))
	for _, node := range agent.Nodes {
		nodes = append(nodes, NodeDefinitionResponse{
			Key:           node.Key,
			DisplayName:   node.DisplayName,
			Description:   node.Description,
			RequiresModel: node.RequiresModel,
		})
	}

	return &AgentResponse{
		ID:          agent.ID,
		AgentKey:    agent.AgentKey,
		Name:        agent.Name,
		Description: agent.Description,
		Nodes:       nodes,
		Status:      string(agent.Status),
		CreatedAt:   agent.CreatedAt,
		UpdatedAt:   agent.UpdatedAt,
	}
}

func toAgentResponses(agents []*AgentDefinition) []AgentResponse {
	responses := make([]AgentResponse, 0, len(agents))
	for _, agent := range agents {
		if agent == nil {
			continue
		}
		responses = append(responses, *toAgentResponse(agent))
	}
	return responses
}

func toConfigResponse(config *AgentConfig) *ConfigResponse {
	if config == nil {
		return nil
	}

	bindings := make([]NodeBindingResponse, 0, len(config.NodeBindings))
	for _, binding := range config.NodeBindings {
		bindings = append(bindings, NodeBindingResponse{
			NodeKey:       binding.NodeKey,
			ModelConfigID: binding.ModelConfigID,
			Temperature:   binding.Temperature,
			MaxTokens:     binding.MaxTokens,
		})
	}

	slices.SortFunc(bindings, func(a, b NodeBindingResponse) int {
		if a.NodeKey < b.NodeKey {
			return -1
		}
		if a.NodeKey > b.NodeKey {
			return 1
		}
		return 0
	})

	return &ConfigResponse{
		ID:           config.ID,
		AgentID:      config.AgentID,
		AgentKey:     config.AgentKey,
		AgentName:    config.AgentName,
		Name:         config.Name,
		Description:  config.Description,
		IsDefault:    config.IsDefault,
		Status:       string(config.Status),
		NodeBindings: bindings,
		CreatedAt:    config.CreatedAt,
		UpdatedAt:    config.UpdatedAt,
	}
}

func toConfigResponses(configs []*AgentConfig) []ConfigResponse {
	responses := make([]ConfigResponse, 0, len(configs))
	for _, config := range configs {
		if config == nil {
			continue
		}
		responses = append(responses, *toConfigResponse(config))
	}
	return responses
}
