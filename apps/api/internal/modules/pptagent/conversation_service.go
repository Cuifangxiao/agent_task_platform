package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
)

func (s *Service) ListConversations(ctx context.Context, userID int64) ([]ConversationSummaryResponse, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.historyRepo == nil {
		return nil, fmt.Errorf("ppt chat history repository is not configured")
	}

	conversations, err := s.historyRepo.ListConversationsByUserID(ctx, userID)
	if err != nil {
		return nil, err
	}

	resp := make([]ConversationSummaryResponse, 0, len(conversations))
	for _, conversation := range conversations {
		if conversation == nil {
			continue
		}
		resp = append(resp, toConversationSummaryResponse(conversation))
	}
	return resp, nil
}

func (s *Service) GetConversation(ctx context.Context, userID int64, id int64) (*ConversationDetailResponse, error) {
	if userID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if id <= 0 {
		return nil, fmt.Errorf("%w: invalid conversation id", ErrInvalidRequest)
	}
	if s.historyRepo == nil {
		return nil, fmt.Errorf("ppt chat history repository is not configured")
	}

	conversation, err := s.historyRepo.GetConversationByID(ctx, userID, id)
	if err != nil {
		return nil, err
	}
	messages, err := s.historyRepo.ListMessagesByConversationID(ctx, userID, id)
	if err != nil {
		return nil, err
	}

	resp := &ConversationDetailResponse{
		ID:              conversation.ID,
		AgentConfigID:   conversation.AgentConfigID,
		AgentConfigName: conversation.AgentConfigName,
		Title:           conversation.Title,
		Status:          conversation.Status,
		LatestRequestID: conversation.LatestRequestID,
		CreatedAt:       conversation.CreatedAt.Format(time.RFC3339),
		UpdatedAt:       conversation.UpdatedAt.Format(time.RFC3339),
		Messages:        make([]map[string]any, 0, len(messages)),
	}
	if s.jobRepo != nil {
		job, jobErr := s.jobRepo.GetLatestJobByConversation(ctx, userID, id)
		if jobErr == nil {
			resp.Job = deckJobResponse(job)
		} else if !errors.Is(jobErr, ErrDeckJobNotFound) {
			return nil, jobErr
		}
	}

	for _, message := range messages {
		if message == nil {
			continue
		}
		item, err := decodeConversationMessage(message)
		if err != nil {
			return nil, err
		}
		resp.Messages = append(resp.Messages, item)
	}

	return resp, nil
}

func (s *Service) DeleteConversation(ctx context.Context, userID int64, id int64) error {
	if userID <= 0 {
		return fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if id <= 0 {
		return fmt.Errorf("%w: invalid conversation id", ErrInvalidRequest)
	}
	if s.historyRepo == nil {
		return fmt.Errorf("ppt chat history repository is not configured")
	}

	requestIDs, err := s.historyRepo.DeleteConversation(ctx, userID, id)
	if err != nil {
		return err
	}
	s.removeConversationArtifacts(requestIDs)
	return nil
}

func (s *Service) removeConversationArtifacts(requestIDs []string) {
	root := filepath.Clean(s.artifactDir)
	for _, requestID := range requestIDs {
		requestID = strings.TrimSpace(requestID)
		if !artifactIDPattern.MatchString(requestID) {
			slog.Warn("skipping invalid ppt artifact id during conversation deletion", "request_id", requestID)
			continue
		}

		target := filepath.Join(root, requestID)
		relative, err := filepath.Rel(root, target)
		if err != nil || relative == "." || filepath.IsAbs(relative) || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			slog.Warn("skipping unsafe ppt artifact path during conversation deletion", "request_id", requestID, "error", err)
			continue
		}
		if err := os.RemoveAll(target); err != nil {
			slog.Warn("failed to remove ppt artifacts after conversation deletion", "request_id", requestID, "error", err)
		}
	}
}

func (s *Service) startConversation(
	ctx context.Context,
	userID int64,
	config *agentconfig.AgentConfig,
	description string,
	fileNames []string,
) (*Conversation, int64, error) {
	conversation, err := s.historyRepo.CreateConversation(ctx, Conversation{
		UserID:          userID,
		AgentConfigID:   config.ID,
		AgentConfigName: config.Name,
		Title:           deriveConversationTitle(description),
		Status:          ConversationStatusRunning,
	})
	if err != nil {
		return nil, 0, err
	}

	userContent := map[string]any{"text": description}
	if len(fileNames) > 0 {
		userContent["fileNames"] = fileNames
	}

	if _, err := s.historyRepo.CreateMessage(ctx, ConversationMessage{
		ConversationID: conversation.ID,
		Role:           "user",
		Kind:           "user",
		ContentJSON:    mustMarshalJSON(userContent),
	}); err != nil {
		return nil, 0, err
	}

	statusContent := persistedStatusContent{
		Title: "正在规划大纲",
		Text:  "我先根据你的描述整理一版 PPT 大纲，稍后发给你确认。",
		State: string(ConversationStatusRunning),
		Steps: []persistedProgressStep{},
	}
	statusMessage, err := s.historyRepo.CreateMessage(ctx, ConversationMessage{
		ConversationID: conversation.ID,
		Role:           "assistant",
		Kind:           "status",
		ContentJSON:    mustMarshalJSON(statusContent),
	})
	if err != nil {
		return nil, 0, err
	}

	return conversation, statusMessage.ID, nil
}

func (s *Service) appendResumeMessages(ctx context.Context, conversationID int64, requestID string, action string) (int64, error) {
	userText := "好的，按这个大纲继续生成完整 PPT。"
	statusTitle := "继续生成 PPT"
	statusText := "我正在根据确认后的大纲展开每一页内容。"
	switch action {
	case "regenerate_outline":
		userText = "请重新生成一版大纲。"
		statusTitle = "重新生成大纲"
		statusText = "我正在重新规划叙事结构和页面重点。"
	case "choose_style":
		userText = "已选择风格，继续生成完整 PPT。"
		statusTitle = "生成页面预览"
		statusText = "我正在按选定风格生成每一页的 HTML 预览。"
	case "regenerate_slide":
		userText = "请按反馈重做这一页。"
		statusTitle = "重做单页"
		statusText = "我正在重做指定页面并重新导出 PPT。"
	}

	if _, err := s.historyRepo.CreateMessage(ctx, ConversationMessage{
		ConversationID: conversationID,
		Role:           "user",
		Kind:           "user",
		RequestID:      requestID,
		ContentJSON:    mustMarshalJSON(map[string]any{"text": userText}),
	}); err != nil {
		return 0, err
	}

	statusContent := persistedStatusContent{
		Title:     statusTitle,
		Text:      statusText,
		RequestID: requestID,
		State:     string(ConversationStatusRunning),
		Steps:     []persistedProgressStep{},
	}
	statusMessage, err := s.historyRepo.CreateMessage(ctx, ConversationMessage{
		ConversationID: conversationID,
		Role:           "assistant",
		Kind:           "status",
		RequestID:      requestID,
		ContentJSON:    mustMarshalJSON(statusContent),
	})
	if err != nil {
		return 0, err
	}

	return statusMessage.ID, nil
}

func (s *Service) markOutlineConfirmed(ctx context.Context, conversationID int64, requestID string) error {
	message, err := s.historyRepo.GetLatestMessageByKindAndRequestID(ctx, conversationID, "outline", requestID)
	if err != nil {
		if errors.Is(err, ErrConversationNotFound) {
			return nil
		}
		return err
	}

	var content map[string]any
	if err := json.Unmarshal([]byte(message.ContentJSON), &content); err != nil {
		return fmt.Errorf("decode outline message: %w", err)
	}
	content["awaitingConfirm"] = false

	return s.historyRepo.UpdateMessageContent(ctx, message.ID, requestID, mustMarshalJSON(content))
}

func (s *Service) markStyleSelected(ctx context.Context, conversationID int64, requestID string) error {
	message, err := s.historyRepo.GetLatestMessageByKindAndRequestID(ctx, conversationID, "style", requestID)
	if err != nil {
		if errors.Is(err, ErrConversationNotFound) {
			return nil
		}
		return err
	}

	var content map[string]any
	if err := json.Unmarshal([]byte(message.ContentJSON), &content); err != nil {
		return fmt.Errorf("decode style message: %w", err)
	}
	content["awaitingConfirm"] = false
	return s.historyRepo.UpdateMessageContent(ctx, message.ID, requestID, mustMarshalJSON(content))
}

func deriveConversationTitle(description string) string {
	description = strings.TrimSpace(description)
	if description == "" {
		return "未命名 PPT 对话"
	}
	if len([]rune(description)) <= 48 {
		return description
	}
	runes := []rune(description)
	return string(runes[:48]) + "..."
}

func decodeConversationMessage(message *ConversationMessage) (map[string]any, error) {
	result := map[string]any{
		"id":   message.ID,
		"role": message.Role,
		"kind": message.Kind,
	}

	var content map[string]any
	if err := json.Unmarshal([]byte(message.ContentJSON), &content); err != nil {
		return nil, fmt.Errorf("decode conversation message content: %w", err)
	}
	for key, value := range content {
		result[key] = value
	}
	return result, nil
}

func toConversationSummaryResponse(conversation *Conversation) ConversationSummaryResponse {
	return ConversationSummaryResponse{
		ID:              conversation.ID,
		AgentConfigID:   conversation.AgentConfigID,
		AgentConfigName: conversation.AgentConfigName,
		Title:           conversation.Title,
		Status:          conversation.Status,
		LatestRequestID: conversation.LatestRequestID,
		CreatedAt:       conversation.CreatedAt.Format(time.RFC3339),
		UpdatedAt:       conversation.UpdatedAt.Format(time.RFC3339),
	}
}
