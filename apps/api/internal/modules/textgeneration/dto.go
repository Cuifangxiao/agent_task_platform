package textgeneration

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
)

type StreamTextRequest struct {
	ModelConfigID int64  `json:"model_config_id"`
	Message       string `json:"message" binding:"required,min=1,max=20000"`
	SystemPrompt  string `json:"system_prompt" binding:"omitempty,max=20000"`
}

type RuntimeStreamTextRequest struct {
	Provider            string
	Model               string
	APIKey              string
	BaseURL             string
	Prompt              string
	SystemPrompt        string
	Temperature         float64
	MaxTokens           int
	ProviderOptionsJSON string
}

type TextStreamChunk struct {
	RequestID string `json:"request_id"`
	Delta     string `json:"delta,omitempty"`
	Done      bool   `json:"done,omitempty"`
	Error     string `json:"error,omitempty"`
	Sequence  int32  `json:"sequence"`
}

type TextStream interface {
	Recv() (*TextStreamChunk, error)
	Close() error
}

type RuntimeClient interface {
	StreamText(ctx context.Context, user actor.User, req RuntimeStreamTextRequest) (TextStream, error)
}
