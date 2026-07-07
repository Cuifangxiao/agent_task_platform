package pptagent

import (
	"context"
	"encoding/json"

	"agent-task-platform/internal/providercredential"
)

type GenerateDeckRequest struct {
	AgentConfigID  int64  `json:"agent_config_id" binding:"required,min=1"`
	Description    string `json:"description" binding:"required,min=1,max=5000"`
	Audience       string `json:"audience" binding:"omitempty,max=500"`
	Goal           string `json:"goal" binding:"omitempty,max=500"`
	PageCount      int    `json:"page_count" binding:"omitempty,min=1,max=30"`
	Language       string `json:"language" binding:"omitempty,max=64"`
	Style          string `json:"style" binding:"omitempty,max=200"`
	SourceText     string `json:"source_text" binding:"omitempty,max=50000"`
	SourceFileName string `json:"source_file_name"`
	SourceFile     []byte `json:"-"`
}

type RuntimeGenerateDeckRequest struct {
	Description      string
	Audience         string
	Goal             string
	PageCount        int
	Language         string
	Style            string
	SourceText       string
	SourceFileName   string
	SourceFile       []byte
	NodeModelConfigs []RuntimeNodeModelConfig
}

type ResumeDeckRequest struct {
	AgentConfigID int64           `json:"agent_config_id" binding:"required,min=1"`
	RequestID     string          `json:"request_id" binding:"required"`
	Action        string          `json:"action"`
	Outline       json.RawMessage `json:"outline"`
}

type RuntimeResumeDeckRequest struct {
	RequestID        string
	Action           string
	OutlineJSON      string
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
	RequestID   string          `json:"request_id"`
	EventType   string          `json:"event_type,omitempty"`
	Stage       string          `json:"stage,omitempty"`
	Message     string          `json:"message,omitempty"`
	Payload     json.RawMessage `json:"payload,omitempty"`
	ArtifactURL string          `json:"artifact_url,omitempty"`
	Done        bool            `json:"done,omitempty"`
	Error       string          `json:"error,omitempty"`
	Sequence    int32           `json:"sequence"`
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
