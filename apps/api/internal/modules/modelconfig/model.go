package modelconfig

import "time"

type Status string

type ModelType string

const (
	StatusActive   Status = "active"
	StatusInactive Status = "inactive"

	ModelTypeText       ModelType = "text"
	ModelTypeMultimodal ModelType = "multimodal"
	ModelTypeImage      ModelType = "image"
)

type ModelConfig struct {
	ID              int64
	UserID          int64
	Name            string
	Provider        string
	ModelName       string
	ModelType       ModelType
	BaseURL         string
	APIKey          string
	ProviderOptions string
	Temperature     float64
	MaxTokens       int
	IsDefault       bool
	Status          Status
	CreatedAt       time.Time
	UpdatedAt       time.Time
}
