package pptagent

import (
	"agent-task-platform/apps/api/internal/shared/actor"
	"context"
	"encoding/json"
)

type GenerateDeckRequest struct {
	AgentConfigID  int64        `json:"agent_config_id"`
	Description    string       `json:"description" binding:"required,min=1,max=5000"`
	Mode           string       `json:"mode"`
	SourceFileName string       `json:"source_file_name"`
	SourceFile     []byte       `json:"-"`
	SourceFiles    []SourceFile `json:"source_files"`
}

type RuntimeGenerateDeckRequest struct {
	JobID            string
	Mode             string
	Description      string
	SourceFileName   string
	SourceFile       []byte
	SourceFiles      []SourceFile
	NodeModelConfigs []RuntimeNodeModelConfig
}

type SourceFile struct {
	FileName   string `json:"filename"`
	Content    []byte `json:"content,omitempty"`
	ResourceID string `json:"resource_id,omitempty"`
	LocalPath  string `json:"-"`
	SHA256     string `json:"sha256,omitempty"`
	SizeBytes  int64  `json:"size_bytes,omitempty"`
}

type ResumeDeckRequest struct {
	RequestID       string          `json:"request_id" binding:"required"`
	OutlineMarkdown string          `json:"outline_markdown"`
	Action          string          `json:"action"`
	Payload         json.RawMessage `json:"payload"`
}

type RuntimeResumeDeckRequest struct {
	JobID            string
	RequestID        string
	OutlineMarkdown  string
	Action           string
	PayloadJSON      string
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
	SupportsImageInput  bool
}

type DeckStreamChunk struct {
	EventVersion    int             `json:"event_version"`
	JobID           string          `json:"job_id,omitempty"`
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
	ErrorCode       string          `json:"error_code,omitempty"`
	StageStatus     string          `json:"stage_status,omitempty"`
	Progress        *DeckProgress   `json:"progress,omitempty"`
	Slide           *DeckSlideEvent `json:"slide,omitempty"`
	Severity        string          `json:"severity,omitempty"`
	ActionRequired  string          `json:"action_required,omitempty"`
	Retryable       bool            `json:"retryable"`
	Sequence        int64           `json:"sequence"`
}

type DeckProgress struct {
	Current int `json:"current"`
	Total   int `json:"total"`
}

type DeckSlideEvent struct {
	Index   int `json:"index"`
	Version int `json:"version"`
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
	Job             *DeckJobResponse   `json:"job,omitempty"`
}

type DeckStream interface {
	Recv() (*DeckStreamChunk, error)
	Close() error
}

type RuntimeClient interface {
	GenerateDeck(
		ctx context.Context,
		user actor.User,
		req RuntimeGenerateDeckRequest,
	) (DeckStream, error)
	ResumeDeck(
		ctx context.Context,
		user actor.User,
		req RuntimeResumeDeckRequest,
	) (DeckStream, error)
}
