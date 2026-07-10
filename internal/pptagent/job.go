package pptagent

import "time"

const DeckEventVersion = 1

type DeckJobStatus string

const (
	DeckJobQueued              DeckJobStatus = "queued"
	DeckJobRunning             DeckJobStatus = "running"
	DeckJobWaitingForOutline   DeckJobStatus = "waiting_for_outline"
	DeckJobWaitingForStyle     DeckJobStatus = "waiting_for_style"
	DeckJobExporting           DeckJobStatus = "exporting"
	DeckJobSuccess             DeckJobStatus = "success"
	DeckJobSuccessWithWarnings DeckJobStatus = "success_with_warnings"
	DeckJobNeedsReview         DeckJobStatus = "needs_review"
	DeckJobFailed              DeckJobStatus = "failed"
	DeckJobCancelled           DeckJobStatus = "cancelled"
)

func (s DeckJobStatus) Terminal() bool {
	switch s {
	case DeckJobSuccess, DeckJobSuccessWithWarnings, DeckJobNeedsReview, DeckJobFailed, DeckJobCancelled:
		return true
	default:
		return false
	}
}

func (s DeckJobStatus) Waiting() bool {
	return s == DeckJobWaitingForOutline || s == DeckJobWaitingForStyle
}

type DeckJob struct {
	ID                string
	UserID            int64
	ConversationID    int64
	AgentConfigID     int64
	RequestID         string
	Mode              string
	Status            DeckJobStatus
	Stage             string
	ErrorCode         string
	ErrorSummary      string
	Retryable         bool
	CancelRequested   bool
	LastEventSequence int64
	InputJSON         string
	QualityJSON       string
	StartedAt         *time.Time
	FinishedAt        *time.Time
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type DeckJobEvent struct {
	ID              int64     `json:"-"`
	EventVersion    int       `json:"event_version"`
	JobID           string    `json:"job_id"`
	RequestID       string    `json:"request_id,omitempty"`
	ConversationID  int64     `json:"conversation_id"`
	Sequence        int64     `json:"sequence"`
	EventType       string    `json:"event_type"`
	Stage           string    `json:"stage"`
	StageStatus     string    `json:"stage_status"`
	Message         string    `json:"message,omitempty"`
	ProgressCurrent int       `json:"progress_current,omitempty"`
	ProgressTotal   int       `json:"progress_total,omitempty"`
	SlideIndex      int       `json:"slide_index,omitempty"`
	SlideVersion    int       `json:"slide_version,omitempty"`
	Severity        string    `json:"severity"`
	ActionRequired  string    `json:"action_required,omitempty"`
	Retryable       bool      `json:"retryable"`
	ErrorCode       string    `json:"error_code,omitempty"`
	Payload         []byte    `json:"-"`
	PayloadValue    any       `json:"payload"`
	CreatedAt       time.Time `json:"created_at"`
}

type DeckJobSource struct {
	FileName     string
	Content      []byte
	ResourceID   string
	RelativePath string
	SHA256       string
	SizeBytes    int64
}

type DeckJobAsset struct {
	JobID         string
	RequestID     string
	Kind          string
	SlideIndex    int
	Version       int
	RelativePath  string
	QualityStatus string
	MetadataJSON  string
}

type CreateDeckJobRequest struct {
	AgentConfigID int64        `json:"agent_config_id"`
	Description   string       `json:"description" binding:"required,min=1,max=5000"`
	Mode          string       `json:"mode"`
	SourceFiles   []SourceFile `json:"-"`
}

type DeckJobActionRequest struct {
	Action          string `json:"action" binding:"required"`
	OutlineMarkdown string `json:"outline_markdown,omitempty"`
	Payload         []byte `json:"-"`
	IdempotencyKey  string `json:"-"`
}

type DeckJobResponse struct {
	ID                string        `json:"job_id"`
	ConversationID    int64         `json:"conversation_id"`
	AgentConfigID     int64         `json:"agent_config_id"`
	RequestID         string        `json:"request_id,omitempty"`
	Mode              string        `json:"mode"`
	Status            DeckJobStatus `json:"status"`
	Stage             string        `json:"stage"`
	ErrorCode         string        `json:"error_code,omitempty"`
	ErrorSummary      string        `json:"error_summary,omitempty"`
	Retryable         bool          `json:"retryable"`
	LastEventSequence int64         `json:"last_event_sequence"`
	Quality           any           `json:"quality"`
	CreatedAt         string        `json:"created_at"`
	UpdatedAt         string        `json:"updated_at"`
}

type DeckProductMetrics struct {
	TotalJobs                int64   `json:"total_jobs"`
	SuccessfulJobs           int64   `json:"successful_jobs"`
	ExportedJobs             int64   `json:"exported_jobs"`
	SuccessRate              float64 `json:"success_rate"`
	DisconnectedJobs         int64   `json:"disconnected_jobs"`
	RecoveredSubscriptions   int64   `json:"recovered_subscriptions"`
	SubscriptionRecoveryRate float64 `json:"subscription_recovery_rate"`
	RegeneratedJobs          int64   `json:"regenerated_jobs"`
	RegenerateRate           float64 `json:"regenerate_rate"`
	SlideRegenerateRate      float64 `json:"slide_regenerate_rate"`
	AverageMustFixIssues     float64 `json:"average_must_fix_issues"`
	AutoRepairSuccessRate    float64 `json:"auto_repair_success_rate"`
	DownloadSuccessRate      float64 `json:"download_success_rate"`
	AverageGenerationMS      float64 `json:"average_generation_ms"`
}
