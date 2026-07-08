package pptagent

import (
	"context"
	"io"
	"testing"
	"time"

	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

func TestResumeDeckAcceptsOutlineMarkdown(t *testing.T) {
	ctx := context.Background()
	requestID := "11111111-1111-1111-1111-111111111111"
	outlineMarkdown := "# Test Deck\n> Narrative\n\n## 1. Cover\n- 核心信息: Hello\n- 版式: title"

	agentRepo := &fakeAgentConfigRepo{
		agent: &agentconfig.AgentDefinition{
			ID:       10,
			AgentKey: agentconfig.AgentKeyPPT,
			Name:     "PPTAgent",
			Status:   agentconfig.StatusActive,
			Nodes: []agentconfig.NodeDefinition{
				{Key: "outline", RequiresModel: true},
				{Key: "slides", RequiresModel: true},
			},
		},
		config: &agentconfig.AgentConfig{
			ID:        20,
			UserID:    7,
			AgentID:   10,
			AgentKey:  agentconfig.AgentKeyPPT,
			Name:      "Default PPTAgent",
			Status:    agentconfig.StatusActive,
			CreatedAt: time.Now(),
			UpdatedAt: time.Now(),
			NodeBindings: []agentconfig.NodeBinding{
				{NodeKey: "outline", ModelConfigID: 100},
				{NodeKey: "slides", ModelConfigID: 100},
			},
		},
	}
	modelRepo := &fakeModelConfigRepo{
		config: &modelconfig.ModelConfig{
			ID:              100,
			UserID:          7,
			Provider:        "openai",
			ModelName:       "gpt-test",
			APIKey:          "test-key",
			ProviderOptions: "{}",
			Temperature:     0.2,
			MaxTokens:       4096,
			Status:          modelconfig.StatusActive,
		},
	}
	historyRepo := &fakeHistoryRepo{
		conversation: &Conversation{
			ID:              30,
			UserID:          7,
			AgentConfigID:   20,
			AgentConfigName: "Default PPTAgent",
			Title:           "Deck",
			Status:          ConversationStatusWaiting,
			LatestRequestID: requestID,
		},
		outlineMessage: &ConversationMessage{
			ID:             40,
			ConversationID: 30,
			Role:           "assistant",
			Kind:           "outline",
			RequestID:      requestID,
			ContentJSON:    `{"outlineMarkdown":"# Old","awaitingConfirm":true}`,
		},
	}
	runtime := &fakeRuntimeClient{}
	service := NewService(modelRepo, agentRepo, historyRepo, runtime, t.TempDir())

	stream, err := service.ResumeDeck(ctx, providercredential.UserContext{UserID: 7}, ResumeDeckRequest{
		RequestID:       requestID,
		OutlineMarkdown: outlineMarkdown,
	})
	if err != nil {
		t.Fatalf("ResumeDeck returned error: %v", err)
	}
	if stream == nil {
		t.Fatal("ResumeDeck returned nil stream")
	}
	if runtime.resumeReq.OutlineMarkdown != outlineMarkdown {
		t.Fatalf("runtime outline markdown = %q, want %q", runtime.resumeReq.OutlineMarkdown, outlineMarkdown)
	}
	if runtime.resumeReq.OutlineMarkdown == "{}" {
		t.Fatal("runtime received legacy JSON outline")
	}
}

type fakeModelConfigRepo struct {
	config *modelconfig.ModelConfig
}

func (r *fakeModelConfigRepo) Create(ctx context.Context, config modelconfig.ModelConfig) (*modelconfig.ModelConfig, error) {
	return nil, nil
}

func (r *fakeModelConfigRepo) ListByUserID(ctx context.Context, userID int64) ([]*modelconfig.ModelConfig, error) {
	return nil, nil
}

func (r *fakeModelConfigRepo) GetByID(ctx context.Context, userID int64, id int64) (*modelconfig.ModelConfig, error) {
	return r.config, nil
}

func (r *fakeModelConfigRepo) Update(ctx context.Context, config modelconfig.ModelConfig) (*modelconfig.ModelConfig, error) {
	return nil, nil
}

func (r *fakeModelConfigRepo) Delete(ctx context.Context, userID int64, id int64) error {
	return nil
}

type fakeAgentConfigRepo struct {
	agent  *agentconfig.AgentDefinition
	config *agentconfig.AgentConfig
}

func (r *fakeAgentConfigRepo) ListAgents(ctx context.Context) ([]*agentconfig.AgentDefinition, error) {
	return []*agentconfig.AgentDefinition{r.agent}, nil
}

func (r *fakeAgentConfigRepo) GetAgentByID(ctx context.Context, id int64) (*agentconfig.AgentDefinition, error) {
	return r.agent, nil
}

func (r *fakeAgentConfigRepo) CreateConfig(ctx context.Context, config agentconfig.AgentConfig) (*agentconfig.AgentConfig, error) {
	return nil, nil
}

func (r *fakeAgentConfigRepo) ListConfigsByUserID(ctx context.Context, userID int64, agentID int64) ([]*agentconfig.AgentConfig, error) {
	return []*agentconfig.AgentConfig{r.config}, nil
}

func (r *fakeAgentConfigRepo) GetConfigByID(ctx context.Context, userID int64, id int64) (*agentconfig.AgentConfig, error) {
	return r.config, nil
}

func (r *fakeAgentConfigRepo) UpdateConfig(ctx context.Context, config agentconfig.AgentConfig) (*agentconfig.AgentConfig, error) {
	return nil, nil
}

func (r *fakeAgentConfigRepo) DeleteConfig(ctx context.Context, userID int64, id int64) error {
	return nil
}

type fakeHistoryRepo struct {
	conversation   *Conversation
	outlineMessage *ConversationMessage
	nextMessageID  int64
}

func (r *fakeHistoryRepo) CreateConversation(ctx context.Context, conversation Conversation) (*Conversation, error) {
	return nil, nil
}

func (r *fakeHistoryRepo) GetConversationByID(ctx context.Context, userID int64, id int64) (*Conversation, error) {
	return r.conversation, nil
}

func (r *fakeHistoryRepo) GetConversationByRequestID(ctx context.Context, userID int64, requestID string) (*Conversation, error) {
	return r.conversation, nil
}

func (r *fakeHistoryRepo) ListConversationsByUserID(ctx context.Context, userID int64) ([]*Conversation, error) {
	return nil, nil
}

func (r *fakeHistoryRepo) CreateMessage(ctx context.Context, message ConversationMessage) (*ConversationMessage, error) {
	r.nextMessageID++
	message.ID = r.nextMessageID
	return &message, nil
}

func (r *fakeHistoryRepo) UpdateMessageContent(ctx context.Context, messageID int64, requestID string, contentJSON string) error {
	return nil
}

func (r *fakeHistoryRepo) GetLatestMessageByKindAndRequestID(ctx context.Context, conversationID int64, kind string, requestID string) (*ConversationMessage, error) {
	return r.outlineMessage, nil
}

func (r *fakeHistoryRepo) ListMessagesByConversationID(ctx context.Context, userID int64, conversationID int64) ([]*ConversationMessage, error) {
	return nil, nil
}

func (r *fakeHistoryRepo) UpdateConversationState(ctx context.Context, conversationID int64, status ConversationStatus, latestRequestID string) error {
	return nil
}

type fakeRuntimeClient struct {
	resumeReq RuntimeResumeDeckRequest
}

func (c *fakeRuntimeClient) GenerateDeck(
	ctx context.Context,
	user providercredential.UserContext,
	req RuntimeGenerateDeckRequest,
) (DeckStream, error) {
	return &fakeDeckStream{}, nil
}

func (c *fakeRuntimeClient) ResumeDeck(
	ctx context.Context,
	user providercredential.UserContext,
	req RuntimeResumeDeckRequest,
) (DeckStream, error) {
	c.resumeReq = req
	return &fakeDeckStream{}, nil
}

type fakeDeckStream struct{}

func (s *fakeDeckStream) Recv() (*DeckStreamChunk, error) {
	return nil, io.EOF
}

func (s *fakeDeckStream) Close() error {
	return nil
}
