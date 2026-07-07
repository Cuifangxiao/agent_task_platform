package agentconfig

import "time"

type NodeBindingRequest struct {
	NodeKey       string   `json:"node_key" binding:"required,min=1,max=64"`
	ModelConfigID int64    `json:"model_config_id" binding:"required,min=1"`
	Temperature   *float64 `json:"temperature"`
	MaxTokens     *int     `json:"max_tokens"`
}

type CreateConfigRequest struct {
	AgentID      int64                `json:"agent_id" binding:"required,min=1"`
	Name         string               `json:"name" binding:"required,min=2,max=64"`
	Description  string               `json:"description" binding:"omitempty,max=255"`
	NodeBindings []NodeBindingRequest `json:"node_bindings" binding:"required"`
	IsDefault    bool                 `json:"is_default"`
	Status       string               `json:"status" binding:"omitempty,max=16"`
}

type UpdateConfigRequest struct {
	Name         *string               `json:"name" binding:"omitempty,min=2,max=64"`
	Description  *string               `json:"description" binding:"omitempty,max=255"`
	NodeBindings *[]NodeBindingRequest `json:"node_bindings"`
	IsDefault    *bool                 `json:"is_default"`
	Status       *string               `json:"status" binding:"omitempty,max=16"`
}

type PathRequest struct {
	ID int64 `uri:"id" binding:"required,min=1"`
}

type AgentResponse struct {
	ID          int64                    `json:"id"`
	AgentKey    string                   `json:"agent_key"`
	Name        string                   `json:"name"`
	Description string                   `json:"description"`
	Nodes       []NodeDefinitionResponse `json:"nodes"`
	Status      string                   `json:"status"`
	CreatedAt   time.Time                `json:"created_at"`
	UpdatedAt   time.Time                `json:"updated_at"`
}

type NodeDefinitionResponse struct {
	Key           string `json:"key"`
	DisplayName   string `json:"display_name"`
	Description   string `json:"description"`
	RequiresModel bool   `json:"requires_model"`
}

type ConfigResponse struct {
	ID           int64                 `json:"id"`
	AgentID      int64                 `json:"agent_id"`
	AgentKey     string                `json:"agent_key"`
	AgentName    string                `json:"agent_name"`
	Name         string                `json:"name"`
	Description  string                `json:"description"`
	IsDefault    bool                  `json:"is_default"`
	Status       string                `json:"status"`
	NodeBindings []NodeBindingResponse `json:"node_bindings"`
	CreatedAt    time.Time             `json:"created_at"`
	UpdatedAt    time.Time             `json:"updated_at"`
}

type NodeBindingResponse struct {
	NodeKey       string   `json:"node_key"`
	ModelConfigID int64    `json:"model_config_id"`
	Temperature   *float64 `json:"temperature,omitempty"`
	MaxTokens     *int     `json:"max_tokens,omitempty"`
}

type DeleteResponse struct {
	Message string `json:"message"`
}
