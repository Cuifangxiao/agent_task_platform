package pptagent

import (
	"context"
	"encoding/json"

	"agent-task-platform/internal/providercredential"
)

type GenerateDeckRequest struct {
	AgentConfigID  int64  `json:"agent_config_id"`
	Description    string `json:"description" binding:"required,min=1,max=5000"`
	SourceFileName string `json:"source_file_name"`
	SourceFile     []byte `json:"-"`
}

type RuntimeGenerateDeckRequest struct {
	Description      string
	SourceFileName   string
	SourceFile       []byte
	NodeModelConfigs []RuntimeNodeModelConfig
}

type ResumeDeckRequest struct {
	RequestID       string `json:"request_id" binding:"required"`
	OutlineMarkdown string `json:"outline_markdown" binding:"required"`
}

type RuntimeResumeDeckRequest struct {
	RequestID        string
	OutlineMarkdown  string
	NodeModelConfigs []RuntimeNodeModelConfig
}

type RuntimeNodeModelConfig struct {
	NodeKey             string
	Provider            string
	Model               string
	APIKey              string
	BaseURL             string
	ProviderOptionsJSON string
	Temperature         float64
	MaxTokens           int
}

type DeckStreamChunk struct {
	ConversationID  int64           `json:"conversation_id,omitempty"`
	RequestID       string          `json:"request_id"`
	EventType       string          `json:"event_type,omitempty"`
	Stage           string          `json:"stage,omitempty"`
	Message         string          `json:"message,omitempty"`
	Channel         string          `json:"channel,omitempty"`
	TextDelta       string          `json:"text_delta,omitempty"`
	Content         string          `json:"content,omitempty"`
	OutlineMarkdown string          `json:"outline_markdown,omitempty"`
	Payload         json.RawMessage `json:"payload,omitempty"`
	ArtifactURL     string          `json:"artifact_url,omitempty"`
	Done            bool            `json:"done,omitempty"`
	Error           string          `json:"error,omitempty"`
	Sequence        int32           `json:"sequence"`
}

type ConversationSummaryResponse struct {
	ID              int64              `json:"id"`
	AgentConfigID   int64              `json:"agent_config_id"`
	AgentConfigName string             `json:"agent_config_name"`
	Title           string             `json:"title"`
	Status          ConversationStatus `json:"status"`
	LatestRequestID string             `json:"latest_request_id,omitempty"`
	CreatedAt       string             `json:"created_at"`
	UpdatedAt       string             `json:"updated_at"`
}

type ConversationDetailResponse struct {
	ID              int64              `json:"id"`
	AgentConfigID   int64              `json:"agent_config_id"`
	AgentConfigName string             `json:"agent_config_name"`
	Title           string             `json:"title"`
	Status          ConversationStatus `json:"status"`
	LatestRequestID string             `json:"latest_request_id,omitempty"`
	CreatedAt       string             `json:"created_at"`
	UpdatedAt       string             `json:"updated_at"`
	Messages        []map[string]any   `json:"messages"`
}

type DeckStream interface {
	Recv() (*DeckStreamChunk, error)
	Close() error
}

type RuntimeClient interface {
	GenerateDeck(
		ctx context.Context,
		user providercredential.UserContext,
		req RuntimeGenerateDeckRequest,
	) (DeckStream, error)
	ResumeDeck(
		ctx context.Context,
		user providercredential.UserContext,
		req RuntimeResumeDeckRequest,
	) (DeckStream, error)
}
