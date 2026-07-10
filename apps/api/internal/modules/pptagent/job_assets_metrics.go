package pptagent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

func (s *Service) ProductMetrics(ctx context.Context, userID int64) (*DeckProductMetrics, error) {
	repo, ok := s.jobRepo.(JobMetricsRepository)
	if !ok {
		return nil, fmt.Errorf("ppt deck metrics repository is not configured")
	}
	return repo.ProductMetrics(ctx, userID)
}

func (s *Service) RecordJobSubscriptionResume(ctx context.Context, userID int64, jobID string, after int64) {
	if after <= 0 || s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByID(ctx, userID, jobID)
	if err == nil {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "subscription_resumed", Stage: job.Stage, StageStatus: "running", Message: "SSE subscription resumed", Severity: "info"})
	}
}

func (s *Service) RecordJobSubscriptionDisconnect(ctx context.Context, userID int64, jobID string) {
	if s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByID(ctx, userID, jobID)
	if err == nil && !job.Status.Terminal() && !job.Status.Waiting() {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "subscription_disconnected", Stage: job.Stage, StageStatus: "running", Message: "SSE subscriber disconnected while job continued", Severity: "info"})
	}
}

func (s *Service) RecordArtifactDownload(ctx context.Context, userID int64, requestID string) {
	if s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByRequestID(ctx, userID, requestID)
	if err == nil {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "artifact_downloaded", Stage: "export", StageStatus: "done", Message: "Artifact download served", Severity: "info", RequestID: requestID})
	}
}

func (s *Service) persistJobSources(jobID string, files []SourceFile) ([]DeckJobSource, func(), error) {
	noop := func() {}
	if len(files) == 0 {
		return nil, noop, nil
	}
	root := filepath.Join(filepath.Dir(s.artifactDir), "deck-sources")
	jobDir := filepath.Join(root, jobID)
	if err := os.MkdirAll(jobDir, 0o700); err != nil {
		return nil, noop, fmt.Errorf("create deck source directory: %w", err)
	}
	cleanup := func() { _ = os.RemoveAll(jobDir) }
	sources := make([]DeckJobSource, 0, len(files))
	for _, file := range files {
		resourceID, err := randomUUID()
		if err != nil {
			cleanup()
			return nil, noop, fmt.Errorf("create deck source id: %w", err)
		}
		ext := strings.ToLower(filepath.Ext(file.FileName))
		if len(ext) > 12 || !regexp.MustCompile(`^\.[a-z0-9]+$`).MatchString(ext) {
			ext = ""
		}
		name := resourceID + ext
		path := filepath.Join(jobDir, name)
		if err := os.WriteFile(path, file.Content, 0o600); err != nil {
			cleanup()
			return nil, noop, fmt.Errorf("persist deck source: %w", err)
		}
		digest := sha256.Sum256(file.Content)
		sources = append(sources, DeckJobSource{
			FileName: file.FileName, ResourceID: resourceID,
			RelativePath: filepath.Join(jobID, name), SHA256: hex.EncodeToString(digest[:]), SizeBytes: int64(len(file.Content)),
		})
	}
	return sources, cleanup, nil
}

func (s *Service) runtimeSourceFile(source DeckJobSource) (SourceFile, error) {
	if strings.TrimSpace(source.RelativePath) == "" {
		return SourceFile{FileName: source.FileName, Content: source.Content, SizeBytes: int64(len(source.Content))}, nil
	}
	root := filepath.Join(filepath.Dir(s.artifactDir), "deck-sources")
	path := filepath.Join(root, filepath.Clean(source.RelativePath))
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return SourceFile{}, fmt.Errorf("invalid deck source resource path")
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return SourceFile{}, fmt.Errorf("deck source resource is unavailable")
	}
	if source.SizeBytes > 0 && info.Size() != source.SizeBytes {
		return SourceFile{}, fmt.Errorf("deck source resource size mismatch")
	}
	return SourceFile{
		FileName: source.FileName, ResourceID: source.ResourceID, LocalPath: path,
		SHA256: source.SHA256, SizeBytes: info.Size(),
	}, nil
}

func payloadHasWarnings(raw json.RawMessage) bool {
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return false
	}
	if warnings, ok := payload["warnings"].([]any); ok && len(warnings) > 0 {
		return true
	}
	return strings.EqualFold(payloadString(payload["status"]), "warning") ||
		strings.EqualFold(payloadString(payload["quality_status"]), "success_with_warnings") ||
		payload["degraded"] == true
}

func payloadNeedsReview(raw json.RawMessage) bool {
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return false
	}
	return payload["needs_review"] == true || strings.EqualFold(payloadString(payload["quality_status"]), "needs_review")
}

func (s *Service) persistAssetFromChunk(ctx context.Context, job *DeckJob, chunk *DeckStreamChunk) error {
	if chunk == nil || chunk.RequestID == "" || len(chunk.Payload) == 0 {
		return nil
	}
	var payload map[string]any
	if json.Unmarshal(chunk.Payload, &payload) != nil {
		return nil
	}
	if chunk.EventType == "slide_preview" {
		for _, kind := range []string{"png", "html"} {
			path := payloadString(payload[kind])
			if path == "" {
				continue
			}
			_ = s.jobRepo.UpsertAsset(ctx, DeckJobAsset{JobID: job.ID, RequestID: chunk.RequestID, Kind: kind,
				SlideIndex: payloadInt(payload["index"]), Version: maxInt(1, payloadInt(payload["version"])), RelativePath: path,
				QualityStatus: payloadString(payload["status"]), MetadataJSON: string(chunk.Payload)})
		}
	}
	if chunk.EventType == "artifact" {
		path := payloadString(payload["relative_path"])
		if path == "" {
			path = "deck.pptx"
		}
		return s.jobRepo.UpsertAsset(ctx, DeckJobAsset{JobID: job.ID, RequestID: chunk.RequestID, Kind: "pptx",
			Version: 1, RelativePath: path, QualityStatus: qualityStatusForPayload(chunk.Payload), MetadataJSON: string(chunk.Payload)})
	}
	return nil
}

func qualityStatusForPayload(raw json.RawMessage) string {
	if payloadNeedsReview(raw) {
		return "needs_review"
	}
	if payloadHasWarnings(raw) {
		return "warning"
	}
	return "ready"
}

func progressCurrent(progress *DeckProgress) int {
	if progress == nil {
		return 0
	}
	return progress.Current
}
func progressTotal(progress *DeckProgress) int {
	if progress == nil {
		return 0
	}
	return progress.Total
}
func slideIndex(slide *DeckSlideEvent) int {
	if slide == nil {
		return 0
	}
	return slide.Index
}
func slideVersion(slide *DeckSlideEvent) int {
	if slide == nil {
		return 0
	}
	return slide.Version
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
