package pptagent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"
)

type jobEventStream struct {
	ctx     context.Context
	repo    JobRepository
	userID  int64
	jobID   string
	after   int64
	pending []DeckJobEvent
}

func (s *jobEventStream) Recv() (*DeckStreamChunk, error) {
	for {
		if len(s.pending) > 0 {
			event := s.pending[0]
			s.pending = s.pending[1:]
			s.after = event.Sequence
			return eventToChunk(event), nil
		}
		events, err := s.repo.ListEvents(s.ctx, s.userID, s.jobID, s.after, 200)
		if err != nil {
			return nil, err
		}
		if len(events) > 0 {
			s.pending = events
			continue
		}
		job, err := s.repo.GetJobByID(s.ctx, s.userID, s.jobID)
		if err != nil {
			return nil, err
		}
		if job.Status.Terminal() || job.Status.Waiting() {
			return nil, io.EOF
		}
		select {
		case <-s.ctx.Done():
			return nil, s.ctx.Err()
		case <-time.After(350 * time.Millisecond):
		}
	}
}

func (s *jobEventStream) Close() error { return nil }

func eventToChunk(event DeckJobEvent) *DeckStreamChunk {
	chunk := &DeckStreamChunk{
		EventVersion: event.EventVersion, JobID: event.JobID, ConversationID: event.ConversationID,
		RequestID: event.RequestID, EventType: event.EventType, Stage: event.Stage, StageStatus: event.StageStatus,
		Message: event.Message, Severity: event.Severity, ActionRequired: event.ActionRequired, Retryable: event.Retryable,
		ErrorCode: event.ErrorCode, Payload: json.RawMessage(event.Payload), Sequence: event.Sequence,
	}
	if event.ProgressCurrent > 0 || event.ProgressTotal > 0 {
		chunk.Progress = &DeckProgress{Current: event.ProgressCurrent, Total: event.ProgressTotal}
	}
	if event.SlideIndex > 0 {
		chunk.Slide = &DeckSlideEvent{Index: event.SlideIndex, Version: event.SlideVersion}
	}
	if event.EventType == "error" {
		chunk.Error = event.Message
	}
	switch event.EventType {
	case "interrupt", "done", "error", "cancelled":
		chunk.Done = true
	}
	return chunk
}

func normalizeChunkProtocol(chunk *DeckStreamChunk) {
	if chunk.StageStatus == "" {
		switch chunk.EventType {
		case "interrupt":
			chunk.StageStatus = "waiting"
		case "done", "artifact":
			chunk.StageStatus = "done"
		case "error":
			chunk.StageStatus = "error"
		default:
			chunk.StageStatus = "running"
		}
	}
	if chunk.Severity == "" {
		if chunk.Error != "" || chunk.StageStatus == "error" {
			chunk.Severity = "error"
		} else if payloadHasWarnings(chunk.Payload) {
			chunk.Severity = "warning"
		} else {
			chunk.Severity = "info"
		}
	}
	if chunk.ActionRequired == "" && chunk.EventType == "interrupt" {
		if chunk.Stage == "style_review" {
			chunk.ActionRequired = "choose_style"
		} else {
			chunk.ActionRequired = "approve_outline"
		}
	}
	if chunk.Progress == nil || chunk.Slide == nil {
		var payload map[string]any
		_ = json.Unmarshal(chunk.Payload, &payload)
		if chunk.Progress == nil {
			current, total := payloadInt(payload["current"]), payloadInt(payload["total"])
			if current > 0 || total > 0 {
				chunk.Progress = &DeckProgress{Current: current, Total: total}
			}
		}
		if chunk.Slide == nil {
			index := payloadInt(payload["index"])
			if index > 0 {
				chunk.Slide = &DeckSlideEvent{Index: index, Version: payloadInt(payload["version"])}
			}
		}
	}
}

func jobStatusFromChunk(chunk *DeckStreamChunk) DeckJobStatus {
	if chunk.EventType == "interrupt" {
		if chunk.Stage == "style_review" {
			return DeckJobWaitingForStyle
		}
		return DeckJobWaitingForOutline
	}
	if chunk.EventType == "error" {
		return DeckJobFailed
	}
	if chunk.EventType == "artifact" {
		return DeckJobExporting
	}
	if chunk.Done && chunk.EventType == "done" {
		return finalJobStatus(chunk.Payload)
	}
	if chunk.Stage == "export" && !chunk.Done {
		return DeckJobExporting
	}
	return DeckJobRunning
}

func finalJobStatus(quality json.RawMessage) DeckJobStatus {
	if payloadNeedsReview(quality) {
		return DeckJobNeedsReview
	}
	if payloadHasWarnings(quality) {
		return DeckJobSuccessWithWarnings
	}
	return DeckJobSuccess
}

func normalizeDeckStage(stage string) string {
	switch strings.TrimSpace(stage) {
	case "slides", "write_slides":
		return "writer"
	case "design_slides", "render":
		return "designer"
	case "review":
		return "review_vlm"
	case "done":
		return "export"
	default:
		return strings.TrimSpace(stage)
	}
}

func validateActionState(status DeckJobStatus, action string) error {
	switch action {
	case "approve_outline", "regenerate_outline":
		if status != DeckJobWaitingForOutline {
			return fmt.Errorf("%w: outline approval is not expected", ErrInvalidRequest)
		}
	case "choose_style":
		if status != DeckJobWaitingForStyle {
			return fmt.Errorf("%w: style selection is not expected", ErrInvalidRequest)
		}
	case "regenerate_slide":
		if status != DeckJobSuccess && status != DeckJobSuccessWithWarnings && status != DeckJobNeedsReview {
			return fmt.Errorf("%w: slide regeneration requires an exported deck", ErrInvalidRequest)
		}
	default:
		return fmt.Errorf("%w: unsupported deck action %q", ErrInvalidRequest, action)
	}
	return nil
}
