package pptagent

import (
	"context"
	"encoding/json"

	"agent-task-platform/internal/providercredential"
)

type GenerateDeckRequest struct {
	AgentConfigID int64  `json:"agent_config_id" binding:"required,min=1"`
	Topic         string `json:"topic" binding:"required,min=1,max=500"`
	Audience      string `json:"audience" binding:"omitempty,max=500"`
	Goal          string `json:"goal" binding:"omitempty,max=500"`
	PageCount     int    `json:"page_count" binding:"omitempty,min=1,max=30"`
	Language      string `json:"language" binding:"omitempty,max=64"`
	Style         string `json:"style" binding:"omitempty,max=200"`
	SourceText    string `json:"source_text" binding:"omitempty,max=50000"`
}

type RuntimeGenerateDeckRequest struct {
	Topic            string
	Audience         string
	Goal             string
	PageCount        int
	Language         string
	Style            string
	SourceText       string
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
}
