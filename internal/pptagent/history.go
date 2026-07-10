package pptagent

import "time"

type ConversationStatus string

const (
	ConversationStatusRunning   ConversationStatus = "running"
	ConversationStatusWaiting   ConversationStatus = "waiting"
	ConversationStatusDone      ConversationStatus = "done"
	ConversationStatusError     ConversationStatus = "error"
	ConversationStatusCancelled ConversationStatus = "cancelled"
)

type Conversation struct {
	ID              int64
	UserID          int64
	AgentConfigID   int64
	AgentConfigName string
	Title           string
	Status          ConversationStatus
	LatestRequestID string
	CreatedAt       time.Time
	UpdatedAt       time.Time
}

type ConversationMessage struct {
	ID             int64
	ConversationID int64
	Role           string
	Kind           string
	RequestID      string
	ContentJSON    string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}
