package modelconfig

import "time"

type CreateRequest struct {
	Name        string   `json:"name" binding:"required,min=2,max=64"`
	Provider    string   `json:"provider" binding:"required,min=2,max=32"`
	ModelName   string   `json:"model_name" binding:"required,min=1,max=128"`
	BaseURL     string   `json:"base_url" binding:"omitempty,max=255"`
	APIKey      string   `json:"api_key" binding:"required,min=1,max=512"`
	Temperature *float64 `json:"temperature"`
	MaxTokens   *int     `json:"max_tokens"`
	IsDefault   bool     `json:"is_default"`
	Status      string   `json:"status"`
}

type Response struct {
	ID           int64     `json:"id"`
	Name         string    `json:"name"`
	Provider     string    `json:"provider"`
	ModelName    string    `json:"model_name"`
	BaseURL      string    `json:"base_url"`
	APIKeyMasked string    `json:"api_key_masked"`
	HasAPIKey    bool      `json:"has_api_key"`
	Temperature  float64   `json:"temperature"`
	MaxTokens    int       `json:"max_tokens"`
	IsDefault    bool      `json:"is_default"`
	Status       string    `json:"status"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}
