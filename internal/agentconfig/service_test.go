package agentconfig

import (
	"context"
	"errors"
	"testing"
	"time"

	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

type stubRepo struct {
	createCalls int
	lastCreated AgentConfig
	agents      map[int64]*AgentDefinition
}

func (s *stubRepo) ListAgents(context.Context) ([]*AgentDefinition, error) {
	agents := make([]*AgentDefinition, 0, len(s.agents))
	for _, agent := range s.agents {
		agents = append(agents, agent)
	}
	return agents, nil
}

func (s *stubRepo) GetAgentByID(_ context.Context, id int64) (*AgentDefinition, error) {
	agent, ok := s.agents[id]
	if !ok {
		return nil, ErrNotFound
	}
	return agent, nil
}

func (s *stubRepo) CreateConfig(_ context.Context, config AgentConfig) (*AgentConfig, error) {
	s.createCalls++
	s.lastCreated = config
	now := time.Now()
	config.ID = 10
	config.AgentKey = AgentKeyPPT
	config.AgentName = "PPTAgent"
	config.CreatedAt = now
	config.UpdatedAt = now
	return &config, nil
}

func (s *stubRepo) ListConfigsByUserID(context.Context, int64, int64) ([]*AgentConfig, error) {
	return nil, nil
}

func (s *stubRepo) GetConfigByID(context.Context, int64, int64) (*AgentConfig, error) {
	return nil, ErrNotFound
}

func (s *stubRepo) UpdateConfig(context.Context, AgentConfig) (*AgentConfig, error) {
	return nil, ErrNotFound
}

func (s *stubRepo) DeleteConfig(context.Context, int64, int64) error {
	return nil
}

type stubModelConfigRepo struct {
	byID map[int64]*modelconfig.ModelConfig
}

func (s *stubModelConfigRepo) GetByID(_ context.Context, _ int64, id int64) (*modelconfig.ModelConfig, error) {
	config, ok := s.byID[id]
	if !ok {
		return nil, modelconfig.ErrNotFound
	}
	return config, nil
}

func TestCreateValidatesAndSavesAgentConfigBindings(t *testing.T) {
	repo := &stubRepo{agents: map[int64]*AgentDefinition{1: testPPTAgent()}}
	modelRepo := &stubModelConfigRepo{byID: map[int64]*modelconfig.ModelConfig{
		1: {ID: 1, Status: modelconfig.StatusActive},
		2: {ID: 2, Status: modelconfig.StatusActive},
		3: {ID: 3, Status: modelconfig.StatusActive},
	}}
	service := NewService(repo, modelRepo)

	temperature := 0.4
	maxTokens := 4096
	resp, err := service.CreateConfig(context.Background(), testUser(), CreateConfigRequest{
		AgentID:     1,
		Name:        "balanced ppt",
		Description: "balanced node models",
		IsDefault:   true,
		NodeBindings: []NodeBindingRequest{
			{NodeKey: "outline", ModelConfigID: 1, Temperature: &temperature},
			{NodeKey: "slides", ModelConfigID: 2, MaxTokens: &maxTokens},
			{NodeKey: "review", ModelConfigID: 3},
		},
	})
	if err != nil {
		t.Fatalf("CreateConfig returned error: %v", err)
	}

	if repo.createCalls != 1 {
		t.Fatalf("expected create to be called once, got %d", repo.createCalls)
	}
	if repo.lastCreated.AgentID != 1 {
		t.Fatalf("expected agent id 1, got %d", repo.lastCreated.AgentID)
	}
	if len(resp.NodeBindings) != 3 {
		t.Fatalf("expected 3 node bindings, got %d", len(resp.NodeBindings))
	}
}

func TestCreateRejectsMissingRequiredNode(t *testing.T) {
	service := NewService(
		&stubRepo{agents: map[int64]*AgentDefinition{1: testPPTAgent()}},
		&stubModelConfigRepo{byID: map[int64]*modelconfig.ModelConfig{
			1: {ID: 1, Status: modelconfig.StatusActive},
			2: {ID: 2, Status: modelconfig.StatusActive},
		}},
	)

	_, err := service.CreateConfig(context.Background(), testUser(), CreateConfigRequest{
		AgentID: 1,
		Name:    "incomplete",
		NodeBindings: []NodeBindingRequest{
			{NodeKey: "outline", ModelConfigID: 1},
			{NodeKey: "slides", ModelConfigID: 2},
		},
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
}

func TestCreateRejectsInactiveModelConfig(t *testing.T) {
	service := NewService(
		&stubRepo{agents: map[int64]*AgentDefinition{1: testPPTAgent()}},
		&stubModelConfigRepo{byID: map[int64]*modelconfig.ModelConfig{
			1: {ID: 1, Status: modelconfig.StatusActive},
			2: {ID: 2, Status: modelconfig.StatusInactive},
			3: {ID: 3, Status: modelconfig.StatusActive},
		}},
	)

	_, err := service.CreateConfig(context.Background(), testUser(), CreateConfigRequest{
		AgentID: 1,
		Name:    "inactive model",
		NodeBindings: []NodeBindingRequest{
			{NodeKey: "outline", ModelConfigID: 1},
			{NodeKey: "slides", ModelConfigID: 2},
			{NodeKey: "review", ModelConfigID: 3},
		},
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected ErrInvalidRequest, got %v", err)
	}
}

func testPPTAgent() *AgentDefinition {
	return &AgentDefinition{
		ID:       1,
		AgentKey: AgentKeyPPT,
		Name:     "PPTAgent",
		Status:   StatusActive,
		Nodes: []NodeDefinition{
			{Key: "outline", RequiresModel: true},
			{Key: "slides", RequiresModel: true},
			{Key: "review", RequiresModel: true},
		},
	}
}

func testUser() providercredential.UserContext {
	return providercredential.UserContext{
		UserID:    1,
		Username:  "alice",
		SessionID: "session-1",
		Device:    "web",
	}
}
