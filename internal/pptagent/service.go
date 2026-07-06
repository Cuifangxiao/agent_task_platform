package pptagent

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

var ErrInvalidRequest = errors.New("invalid ppt agent request")
var ErrModelConfigNotFound = errors.New("model config not found")
var ErrModelConfigInactive = errors.New("model config is inactive")
var ErrArtifactNotFound = errors.New("deck artifact not found")

var artifactIDPattern = regexp.MustCompile(`^[a-fA-F0-9-]{36}$`)

type Service struct {
	modelConfigRepo modelconfig.Repository
	runtimeClient   RuntimeClient
	artifactDir     string
}

func NewService(modelConfigRepo modelconfig.Repository, runtimeClient RuntimeClient, artifactDir string) *Service {
	if strings.TrimSpace(artifactDir) == "" {
		artifactDir = filepath.Join("python-agent", "artifacts", "decks")
	}
	if abs, err := filepath.Abs(artifactDir); err == nil {
		artifactDir = abs
	}

	return &Service{
		modelConfigRepo: modelConfigRepo,
		runtimeClient:   runtimeClient,
		artifactDir:     artifactDir,
	}
}

func (s *Service) GenerateDeck(
	ctx context.Context,
	user providercredential.UserContext,
	req GenerateDeckRequest,
) (DeckStream, error) {
	if user.UserID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.modelConfigRepo == nil {
		return nil, fmt.Errorf("model config repository is not configured")
	}
	if s.runtimeClient == nil {
		return nil, fmt.Errorf("runtime client is not configured")
	}

	topic := strings.TrimSpace(req.Topic)
	if topic == "" {
		return nil, fmt.Errorf("%w: topic is required", ErrInvalidRequest)
	}

	config, err := s.resolveModelConfig(ctx, user.UserID, req.ModelConfigID)
	if err != nil {
		return nil, err
	}
	if config.Status != modelconfig.StatusActive {
		return nil, ErrModelConfigInactive
	}

	providerOptions := strings.TrimSpace(config.ProviderOptions)
	if providerOptions == "" {
		providerOptions = "{}"
	}

	return s.runtimeClient.GenerateDeck(ctx, user, RuntimeGenerateDeckRequest{
		Provider:            config.Provider,
		Model:               config.ModelName,
		APIKey:              config.APIKey,
		BaseURL:             config.BaseURL,
		ProviderOptionsJSON: providerOptions,
		Temperature:         config.Temperature,
		MaxTokens:           config.MaxTokens,
		Topic:               topic,
		Audience:            defaultString(req.Audience, "general audience"),
		Goal:                defaultString(req.Goal, "explain the topic clearly"),
		PageCount:           clampPageCount(req.PageCount),
		Language:            defaultString(req.Language, "zh-CN"),
		Style:               defaultString(req.Style, "concise business deck"),
		SourceText:          strings.TrimSpace(req.SourceText),
	})
}

func (s *Service) ArtifactPath(requestID string) (string, error) {
	requestID = strings.TrimSpace(requestID)
	if !artifactIDPattern.MatchString(requestID) {
		return "", fmt.Errorf("%w: invalid request id", ErrInvalidRequest)
	}

	path := filepath.Join(s.artifactDir, requestID+".pptx")
	cleanDir := filepath.Clean(s.artifactDir)
	cleanPath := filepath.Clean(path)
	rel, err := filepath.Rel(cleanDir, cleanPath)
	if err != nil || strings.HasPrefix(rel, "..") || filepath.IsAbs(rel) {
		return "", fmt.Errorf("%w: invalid artifact path", ErrInvalidRequest)
	}

	info, err := os.Stat(cleanPath)
	if err != nil {
		if os.IsNotExist(err) {
			return "", ErrArtifactNotFound
		}
		return "", err
	}
	if info.IsDir() {
		return "", ErrArtifactNotFound
	}

	return cleanPath, nil
}

func (s *Service) resolveModelConfig(ctx context.Context, userID int64, id int64) (*modelconfig.ModelConfig, error) {
	if id > 0 {
		config, err := s.modelConfigRepo.GetByID(ctx, userID, id)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return nil, ErrModelConfigNotFound
			}
			return nil, err
		}
		return config, nil
	}

	configs, err := s.modelConfigRepo.ListByUserID(ctx, userID)
	if err != nil {
		return nil, err
	}

	var firstActive *modelconfig.ModelConfig
	for _, config := range configs {
		if config == nil || config.Status != modelconfig.StatusActive {
			continue
		}
		if config.IsDefault {
			return config, nil
		}
		if firstActive == nil {
			firstActive = config
		}
	}
	if firstActive != nil {
		return firstActive, nil
	}

	return nil, ErrModelConfigNotFound
}

func defaultString(value string, fallback string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback
	}
	return value
}

func clampPageCount(value int) int {
	if value <= 0 {
		return 8
	}
	if value > 30 {
		return 30
	}
	return value
}
