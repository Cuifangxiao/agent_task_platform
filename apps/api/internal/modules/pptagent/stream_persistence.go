package pptagent

import (
	"context"
	"encoding/json"
	"strings"
)

type persistedProgressStep struct {
	ID      int64  `json:"id"`
	Label   string `json:"label"`
	Message string `json:"message"`
	Tone    string `json:"tone"`
	Stage   string `json:"stage,omitempty"`
}

type persistedSlideProgress struct {
	Index            int      `json:"index"`
	Title            string   `json:"title,omitempty"`
	KeyMessage       string   `json:"keyMessage,omitempty"`
	Layout           string   `json:"layout,omitempty"`
	Component        string   `json:"component,omitempty"`
	Bullets          []string `json:"bullets,omitempty"`
	VisualSuggestion string   `json:"visualSuggestion,omitempty"`
	SpeakerNotes     string   `json:"speakerNotes,omitempty"`
	PreviewPNG       string   `json:"previewPng,omitempty"`
	PreviewHTML      string   `json:"previewHtml,omitempty"`
	PreviewThumb     string   `json:"previewThumb,omitempty"`
	Version          int      `json:"version,omitempty"`
	Status           string   `json:"status,omitempty"`
	Warnings         []string `json:"warnings,omitempty"`
}

type persistedStatusContent struct {
	Title     string                   `json:"title"`
	Text      string                   `json:"text"`
	RequestID string                   `json:"requestId,omitempty"`
	State     string                   `json:"state"`
	Steps     []persistedProgressStep  `json:"steps"`
	Slides    []persistedSlideProgress `json:"slides,omitempty"`
}

type persistedDeckStream struct {
	ctx              context.Context
	historyRepo      HistoryRepository
	conversationID   int64
	statusMessageID  int64
	statusContent    persistedStatusContent
	resultMessageID  int64
	outlineMessageID int64
	styleMessageID   int64
	stream           DeckStream
}

func newPersistedDeckStream(
	ctx context.Context,
	historyRepo HistoryRepository,
	conversationID int64,
	statusMessageID int64,
	stream DeckStream,
) *persistedDeckStream {
	return &persistedDeckStream{
		ctx:             ctx,
		historyRepo:     historyRepo,
		conversationID:  conversationID,
		statusMessageID: statusMessageID,
		stream:          stream,
	}
}

func (s *persistedDeckStream) Recv() (*DeckStreamChunk, error) {
	chunk, err := s.stream.Recv()
	if err != nil || chunk == nil {
		return chunk, err
	}

	chunk.ConversationID = s.conversationID
	if err := s.persistChunk(chunk); err != nil {
		return nil, err
	}

	return chunk, nil
}

func (s *persistedDeckStream) Close() error {
	return s.stream.Close()
}

func (s *persistedDeckStream) persistChunk(chunk *DeckStreamChunk) error {
	requestID := strings.TrimSpace(chunk.RequestID)
	if chunk.EventType == "text_delta" && chunk.Stage == "outline" {
		return nil
	}
	snapshot := describePersistedChunk(chunk)

	step := persistedProgressStep{
		ID:      int64(len(s.statusContent.Steps) + 1),
		Label:   stageLabelForPersistence(chunk.Stage, chunk.EventType),
		Message: stepMessageForPersistence(chunk),
		Tone:    toneForPersistence(chunk),
		Stage:   normalizeDeckStage(chunk.Stage),
	}
	s.statusContent.Steps = append(s.statusContent.Steps, step)
	if len(s.statusContent.Steps) > 100 {
		s.statusContent.Steps = append([]persistedProgressStep(nil), s.statusContent.Steps[len(s.statusContent.Steps)-100:]...)
	}
	if requestID != "" {
		s.statusContent.RequestID = requestID
	}
	if snapshot != nil {
		s.statusContent.Title = snapshot.Title
		s.statusContent.Text = snapshot.Text
		s.statusContent.State = snapshot.State
	}
	if slide := persistedSlideFromChunk(chunk); slide != nil {
		s.upsertPersistedSlide(*slide)
	}
	if chunk.EventType == "validation_report" {
		s.applyValidationIssues(chunk.Payload)
	}
	if err := s.historyRepo.UpdateMessageContent(
		s.ctx,
		s.statusMessageID,
		requestID,
		mustMarshalJSON(s.statusContent),
	); err != nil {
		return err
	}

	conversationStatus := ConversationStatusRunning
	switch s.statusContent.State {
	case string(ConversationStatusWaiting):
		conversationStatus = ConversationStatusWaiting
	case string(ConversationStatusDone):
		conversationStatus = ConversationStatusDone
	case string(ConversationStatusError):
		conversationStatus = ConversationStatusError
	}
	if err := s.historyRepo.UpdateConversationState(s.ctx, s.conversationID, conversationStatus, requestID); err != nil {
		return err
	}

	if chunk.EventType == "outline" || (chunk.EventType == "interrupt" && chunk.Stage == "outline_review") {
		if err := s.upsertOutlineMessage(requestID, chunk, chunk.EventType == "interrupt"); err != nil {
			return err
		}
	}
	if chunk.EventType == "style_options" || (chunk.EventType == "interrupt" && chunk.Stage == "style_review") {
		if err := s.upsertStyleMessage(requestID, chunk, chunk.EventType == "interrupt"); err != nil {
			return err
		}
	}
	if chunk.EventType == "artifact" {
		if err := s.upsertResultMessage(requestID, chunk); err != nil {
			return err
		}
	}

	return nil
}

func (s *persistedDeckStream) applyValidationIssues(raw json.RawMessage) {
	var payload struct {
		Issues []struct {
			Page        int    `json:"page"`
			Severity    string `json:"severity"`
			Description string `json:"description"`
		} `json:"issues"`
	}
	if json.Unmarshal(raw, &payload) != nil {
		return
	}
	for _, issue := range payload.Issues {
		if issue.Page <= 0 || strings.TrimSpace(issue.Description) == "" {
			continue
		}
		status := "warning"
		if issue.Severity == "must_fix" {
			status = "error"
		}
		s.upsertPersistedSlide(persistedSlideProgress{Index: issue.Page, Status: status, Warnings: []string{issue.Description}})
	}
}

func (s *persistedDeckStream) upsertPersistedSlide(incoming persistedSlideProgress) {
	if incoming.Index <= 0 {
		return
	}
	if incoming.Status == "" {
		incoming.Status = "pending"
	}

	for index := range s.statusContent.Slides {
		if s.statusContent.Slides[index].Index != incoming.Index {
			continue
		}
		mergePersistedSlide(&s.statusContent.Slides[index], incoming)
		return
	}

	insertAt := len(s.statusContent.Slides)
	for index, slide := range s.statusContent.Slides {
		if incoming.Index < slide.Index {
			insertAt = index
			break
		}
	}
	s.statusContent.Slides = append(s.statusContent.Slides, persistedSlideProgress{})
	copy(s.statusContent.Slides[insertAt+1:], s.statusContent.Slides[insertAt:])
	s.statusContent.Slides[insertAt] = incoming
}

func persistedSlideFromChunk(chunk *DeckStreamChunk) *persistedSlideProgress {
	if chunk == nil || len(chunk.Payload) == 0 {
		return nil
	}
	if chunk.EventType != "slide_started" &&
		chunk.EventType != "slide_ready" &&
		chunk.EventType != "slide_reviewed" &&
		chunk.EventType != "slide_spec_ready" &&
		chunk.EventType != "slide_preview" &&
		!(chunk.EventType == "stage_progress" && (chunk.Stage == "slides" || chunk.Stage == "writer" || chunk.Stage == "designer" || chunk.Stage == "repair")) {
		return nil
	}

	var payload map[string]any
	if err := json.Unmarshal(chunk.Payload, &payload); err != nil {
		return nil
	}
	index := payloadInt(payload["index"])
	if index <= 0 {
		return nil
	}

	warnings := payloadStringSlice(payload["warnings"])
	if warning := payloadString(payload["warning"]); warning != "" {
		warnings = append(warnings, warning)
	}
	status := normalizePersistedSlideStatus(payloadString(payload["status"]))
	switch {
	case chunk.EventType == "slide_started" && status == "":
		status = "running"
	case chunk.EventType == "slide_spec_ready" && status == "":
		status = "running"
	case chunk.EventType == "slide_preview" && status == "":
		status = "ready"
	case chunk.EventType == "slide_ready" && status == "":
		status = "ready"
	case chunk.EventType == "slide_reviewed" && len(warnings) > 0:
		status = "warning"
	case chunk.EventType == "slide_reviewed" && status == "":
		status = "reviewed"
	}

	return &persistedSlideProgress{
		Index:            index,
		Title:            payloadString(payload["title"]),
		KeyMessage:       payloadString(payload["key_message"]),
		Layout:           payloadString(payload["layout"]),
		Component:        payloadString(payload["component"]),
		Bullets:          payloadStringSlice(payload["bullets"]),
		VisualSuggestion: payloadString(payload["visual_suggestion"]),
		SpeakerNotes:     payloadString(payload["speaker_notes"]),
		PreviewPNG:       payloadString(payload["png"]),
		PreviewHTML:      payloadString(payload["html"]),
		PreviewThumb:     "",
		Version:          payloadInt(payload["version"]),
		Status:           status,
		Warnings:         warnings,
	}
}

func mergePersistedSlide(existing *persistedSlideProgress, incoming persistedSlideProgress) {
	if incoming.Title != "" {
		existing.Title = incoming.Title
	}
	if incoming.KeyMessage != "" {
		existing.KeyMessage = incoming.KeyMessage
	}
	if incoming.Layout != "" {
		existing.Layout = incoming.Layout
	}
	if incoming.Component != "" {
		existing.Component = incoming.Component
	}
	if len(incoming.Bullets) > 0 {
		existing.Bullets = incoming.Bullets
	}
	if incoming.VisualSuggestion != "" {
		existing.VisualSuggestion = incoming.VisualSuggestion
	}
	if incoming.SpeakerNotes != "" {
		existing.SpeakerNotes = incoming.SpeakerNotes
	}
	if incoming.PreviewPNG != "" {
		existing.PreviewPNG = incoming.PreviewPNG
	}
	if incoming.PreviewHTML != "" {
		existing.PreviewHTML = incoming.PreviewHTML
	}
	if incoming.PreviewThumb != "" {
		existing.PreviewThumb = incoming.PreviewThumb
	}
	if incoming.Version > 0 {
		existing.Version = incoming.Version
	}
	if incoming.Status != "" && incoming.Status != "pending" {
		existing.Status = incoming.Status
	}
	if len(incoming.Warnings) > 0 {
		existing.Warnings = appendUniqueStrings(existing.Warnings, incoming.Warnings...)
		if existing.Status != "error" {
			existing.Status = "warning"
		}
	}
}

func (s *persistedDeckStream) upsertOutlineMessage(requestID string, chunk *DeckStreamChunk, awaitingConfirm bool) error {
	content := map[string]any{
		"title":           "PPT 大纲",
		"text":            "我先给你一版大纲，确认后继续生成完整 PPT。",
		"requestId":       requestID,
		"outlineMarkdown": extractOutlineMarkdownForPersistence(chunk),
		"awaitingConfirm": awaitingConfirm,
		"showEditor":      false,
	}

	if s.outlineMessageID == 0 {
		message, err := s.historyRepo.CreateMessage(s.ctx, ConversationMessage{
			ConversationID: s.conversationID,
			Role:           "assistant",
			Kind:           "outline",
			RequestID:      requestID,
			ContentJSON:    mustMarshalJSON(content),
		})
		if err != nil {
			return err
		}
		s.outlineMessageID = message.ID
		return nil
	}

	return s.historyRepo.UpdateMessageContent(s.ctx, s.outlineMessageID, requestID, mustMarshalJSON(content))
}

func (s *persistedDeckStream) upsertStyleMessage(requestID string, chunk *DeckStreamChunk, awaitingConfirm bool) error {
	content := map[string]any{
		"title":           "选择 PPT 风格",
		"text":            "我准备了两套视觉方向，选择后继续生成页面。",
		"requestId":       requestID,
		"options":         extractStyleOptionsForPersistence(chunk),
		"awaitingConfirm": awaitingConfirm,
	}

	if s.styleMessageID == 0 {
		message, err := s.historyRepo.CreateMessage(s.ctx, ConversationMessage{
			ConversationID: s.conversationID,
			Role:           "assistant",
			Kind:           "style",
			RequestID:      requestID,
			ContentJSON:    mustMarshalJSON(content),
		})
		if err != nil {
			return err
		}
		s.styleMessageID = message.ID
		return nil
	}

	return s.historyRepo.UpdateMessageContent(s.ctx, s.styleMessageID, requestID, mustMarshalJSON(content))
}

func (s *persistedDeckStream) upsertResultMessage(requestID string, chunk *DeckStreamChunk) error {
	fileName := extractArtifactFileNameForPersistence(chunk.Payload)
	var payload map[string]any
	_ = json.Unmarshal(chunk.Payload, &payload)
	warnings := payloadStringSlice(payload["warnings"])
	qualityStatus := payloadString(payload["quality_status"])
	degraded, _ := payload["degraded"].(bool)
	resultText := "整套内容已经生成完成，你可以直接下载这份 PPT。"
	if qualityStatus == "needs_review" {
		resultText = "PPT 已导出，但仍有必须人工复核的问题；请先查看质量警告。"
	} else if degraded || len(warnings) > 0 {
		resultText = "PPT 已导出，但部分页面降级为整页图片，文字可能不可编辑。"
	}
	content := map[string]any{
		"title":         "PPT 已准备好",
		"text":          resultText,
		"requestId":     requestID,
		"fileName":      fileName,
		"warnings":      warnings,
		"degraded":      degraded,
		"qualityStatus": qualityStatus,
	}

	if s.resultMessageID == 0 {
		message, err := s.historyRepo.CreateMessage(s.ctx, ConversationMessage{
			ConversationID: s.conversationID,
			Role:           "assistant",
			Kind:           "result",
			RequestID:      requestID,
			ContentJSON:    mustMarshalJSON(content),
		})
		if err != nil {
			return err
		}
		s.resultMessageID = message.ID
		return nil
	}

	return s.historyRepo.UpdateMessageContent(s.ctx, s.resultMessageID, requestID, mustMarshalJSON(content))
}
