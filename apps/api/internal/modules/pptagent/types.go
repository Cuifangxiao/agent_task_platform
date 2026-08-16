package pptagent

import (
	"context"
	"errors"
	"time"
)

var (
	ErrInvalidRequest        = errors.New("invalid ppt task request")
	ErrTaskNotFound          = errors.New("ppt task not found")
	ErrTaskNotReady          = errors.New("ppt task worker is not ready")
	ErrTaskAlreadyFinished   = errors.New("ppt task is already finished")
	ErrTaskStillRunning      = errors.New("running ppt task cannot be deleted")
	ErrEventCursorAhead      = errors.New("event cursor is ahead of the run")
	ErrEventCommitUnknown    = errors.New("ppt event commit outcome is unknown")
	ErrClientRequestConflict = errors.New("client_request_id was already used for a different request")
	ErrThreadRunConflict     = errors.New("thread already has a running ppt task")
	ErrAgentConfigNotFound   = errors.New("agent config not found")
	ErrModelConfigNotFound   = errors.New("model config not found")
	ErrModelConfigInactive   = errors.New("model config is inactive")
	ErrArtifactNotFound      = errors.New("ppt artifact not found")
)

const CurrentSchemaVersion = "1"

type EventType string

const (
	EventTaskStarted   EventType = "TASK_STARTED"
	EventStepStarted   EventType = "STEP_STARTED"
	EventStepProgress  EventType = "STEP_PROGRESS"
	EventStepCompleted EventType = "STEP_COMPLETED"
	EventArtifactReady EventType = "ARTIFACT_READY"
	EventTaskCompleted EventType = "TASK_COMPLETED"
	EventTaskFailed    EventType = "TASK_FAILED"
)

type EventChannel string

const (
	EventChannelLifecycle EventChannel = "lifecycle"
	EventChannelUpdates   EventChannel = "updates"
	EventChannelArtifacts EventChannel = "artifacts"
)

type Stage string

const (
	StagePreparing         Stage = "PREPARING"
	StagePlanning          Stage = "PLANNING"
	StageGeneratingContent Stage = "GENERATING_CONTENT"
	StageGeneratingAssets  Stage = "GENERATING_ASSETS"
	StageGeneratingPages   Stage = "GENERATING_PAGES"
	StageRendering         Stage = "RENDERING"
	StageAssemblingPPT     Stage = "ASSEMBLING_PPT"
	StageFinished          Stage = "FINISHED"
)

type TaskStatus string

const (
	TaskRunning   TaskStatus = "running"
	TaskSucceeded TaskStatus = "succeeded"
	TaskFailed    TaskStatus = "failed"
	TaskCancelled TaskStatus = "cancelled"
)

func (status TaskStatus) Terminal() bool {
	return status == TaskSucceeded || status == TaskFailed || status == TaskCancelled
}

type ResourceType string

const (
	ResourceInputAttachment ResourceType = "INPUT_ATTACHMENT"
	ResourceImage           ResourceType = "IMAGE"
	ResourceSVG             ResourceType = "SVG"
	ResourceHTML            ResourceType = "HTML"
	ResourcePagePreview     ResourceType = "PAGE_PREVIEW"
	ResourcePPTX            ResourceType = "PPTX"
	ResourceOtherFile       ResourceType = "OTHER_FILE"
)

type Progress struct {
	Current uint32  `json:"current"`
	Total   uint32  `json:"total"`
	Percent float32 `json:"percent"`
	Unit    string  `json:"unit"`
}

type ResourceRef struct {
	ResourceID   string       `json:"resource_id"`
	Type         ResourceType `json:"type"`
	Name         string       `json:"name"`
	RelativePath string       `json:"relative_path"`
	URL          string       `json:"url,omitempty"`
	MimeType     string       `json:"mime_type"`
	SizeBytes    uint64       `json:"size_bytes"`
	SHA256       string       `json:"sha256,omitempty"`
}

type PageDetail struct {
	PageID    string `json:"page_id"`
	PageIndex uint32 `json:"page_index"`
	PageCount uint32 `json:"page_count"`
	Title     string `json:"title"`
}

type ErrorDetail struct {
	Code         string `json:"code"`
	Message      string `json:"message"`
	DebugMessage string `json:"debug_message,omitempty"`
	Retryable    bool   `json:"retryable"`
}

type FinalResult struct {
	PPTFile    ResourceRef `json:"ppt_file"`
	SlideCount uint32      `json:"slide_count"`
	ElapsedMS  uint64      `json:"elapsed_ms"`
}

type AgentEvent struct {
	SchemaVersion string        `json:"schema_version"`
	EventID       string        `json:"event_id"`
	Revision      string        `json:"revision,omitempty"`
	ThreadID      string        `json:"thread_id"`
	RunID         string        `json:"run_id"`
	TaskID        string        `json:"task_id"` // Compatibility alias for run_id.
	Sequence      uint64        `json:"sequence"`
	CreatedAt     time.Time     `json:"created_at"`
	Channel       EventChannel  `json:"channel"`
	Type          EventType     `json:"type"`
	Stage         Stage         `json:"stage"`
	Message       string        `json:"message"`
	Progress      *Progress     `json:"progress,omitempty"`
	Resources     []ResourceRef `json:"resources,omitempty"`
	Page          *PageDetail   `json:"page,omitempty"`
	Error         *ErrorDetail  `json:"error,omitempty"`
	FinalResult   *FinalResult  `json:"final_result,omitempty"`
}

func (event AgentEvent) Terminal() bool {
	return event.Type == EventTaskCompleted || event.Type == EventTaskFailed
}

type Task struct {
	SchemaVersion     string       `json:"schema_version"`
	EventID           string       `json:"event_id"` // Most recently applied event; empty before the first event.
	ThreadID          string       `json:"thread_id"`
	RunID             string       `json:"run_id"`
	TaskID            string       `json:"task_id"` // Compatibility alias for run_id.
	ClientRequestID   string       `json:"client_request_id,omitempty"`
	AgentConfigID     int64        `json:"agent_config_id"`
	Name              string       `json:"name"`
	Description       string       `json:"description"`
	Status            TaskStatus   `json:"status"`
	Stage             Stage        `json:"stage"`
	Message           string       `json:"message"`
	LastEventSequence uint64       `json:"last_event_sequence"`
	Result            *FinalResult `json:"result,omitempty"`
	Error             *ErrorDetail `json:"error,omitempty"`
	FileURL           string       `json:"file_url,omitempty"`
	CreatedAt         time.Time    `json:"created_at"`
	UpdatedAt         time.Time    `json:"updated_at"`
	FinishedAt        *time.Time   `json:"finished_at,omitempty"`
	ownerID           int64
	cancel            context.CancelFunc
}

type CreateTaskRequest struct {
	ThreadID        string
	ClientRequestID string
	AgentConfigID   int64
	Name            string
	Description     string
	Attachments     []InputResource
}

type InputResource struct {
	ResourceID string
	Name       string
	MimeType   string
	Content    []byte
	SHA256     string
}

type RuntimeModelConfig struct {
	NodeKey            string
	Provider           string
	Model              string
	APIKey             string
	BaseURL            string
	ProviderOptions    map[string]any
	Temperature        float64
	MaxTokens          int
	SupportsImageInput bool
}

type RuntimeRequest struct {
	ThreadID    string
	RunID       string
	TaskID      string // Compatibility alias for run_id.
	Description string
	Attachments []InputResource
	Models      []RuntimeModelConfig
}

type EventStream interface {
	Recv() (*AgentEvent, error)
	Close() error
}

type RuntimeClient interface {
	GeneratePpt(context.Context, RuntimeRequest) (EventStream, error)
}

type Subscription struct {
	Replay []AgentEvent
	Events <-chan AgentEvent
	Close  func()
}
