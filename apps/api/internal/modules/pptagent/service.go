package pptagent

import (
	"context"
	"errors"
	"path/filepath"
	"regexp"
	"strings"
	"sync"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
)

var ErrInvalidRequest = errors.New("invalid ppt agent request")
var ErrAgentConfigNotFound = errors.New("agent config not found")
var ErrModelConfigNotFound = errors.New("model config not found")
var ErrModelConfigInactive = errors.New("model config is inactive")
var ErrArtifactNotFound = errors.New("deck artifact not found")

var artifactIDPattern = regexp.MustCompile(`^[a-fA-F0-9-]{36}$`)
var unsafeHTMLArtifactPattern = regexp.MustCompile(`(?i)(<\s*(?:script|iframe|object|embed)\b|\bon[a-z]+\s*=|(?:src|href)\s*=\s*["']?\s*(?:https?://|javascript:|data:text/html)|url\(\s*["']?\s*https?://)`)

type Service struct {
	modelConfigRepo modelconfig.Repository
	agentConfigRepo agentconfig.Repository
	historyRepo     HistoryRepository
	jobRepo         JobRepository
	runtimeClient   RuntimeClient
	artifactDir     string
	cancelMu        sync.Mutex
	jobCancels      map[string]context.CancelFunc
}

func NewService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	historyRepo HistoryRepository,
	runtimeClient RuntimeClient,
	artifactDir string,
	jobRepos ...JobRepository,
) *Service {
	if strings.TrimSpace(artifactDir) == "" {
		artifactDir = filepath.Join("apps", "runtime", "artifacts", "decks")
	}
	if abs, err := filepath.Abs(artifactDir); err == nil {
		artifactDir = abs
	}

	var jobRepo JobRepository
	if len(jobRepos) > 0 {
		jobRepo = jobRepos[0]
	}
	return &Service{
		modelConfigRepo: modelConfigRepo,
		agentConfigRepo: agentConfigRepo,
		historyRepo:     historyRepo,
		jobRepo:         jobRepo,
		runtimeClient:   runtimeClient,
		artifactDir:     artifactDir,
		jobCancels:      make(map[string]context.CancelFunc),
	}
}
