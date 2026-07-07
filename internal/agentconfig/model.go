package agentconfig

import "time"

type Status string

const (
	StatusActive   Status = "active"
	StatusInactive Status = "inactive"
)

const AgentKeyPPT = "ppt_agent"

type AgentDefinition struct {
	ID          int64
	AgentKey    string
	Name        string
	Description string
	Nodes       []NodeDefinition
	Status      Status
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

type AgentConfig struct {
	ID           int64
	UserID       int64
	AgentID      int64
	AgentKey     string
	AgentName    string
	Name         string
	Description  string
	IsDefault    bool
	Status       Status
	NodeBindings []NodeBinding
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

type NodeBinding struct {
	ID            int64
	AgentConfigID int64
	NodeKey       string
	ModelConfigID int64
	Temperature   *float64
	MaxTokens     *int
	CreatedAt     time.Time
	UpdatedAt     time.Time
}

type NodeDefinition struct {
	Key           string `json:"key"`
	DisplayName   string `json:"display_name"`
	Description   string `json:"description"`
	RequiresModel bool   `json:"requires_model"`
}
