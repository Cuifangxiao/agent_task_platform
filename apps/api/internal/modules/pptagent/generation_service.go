package pptagent

import (
	"context"
	"fmt"
	"strings"

	"agent-task-platform/apps/api/internal/shared/actor"
)

func (s *Service) GenerateDeck(
	ctx context.Context,
	user actor.User,
	req GenerateDeckRequest,
) (DeckStream, error) {
	if s.jobRepo != nil {
		job, err := s.CreateJob(ctx, user, CreateDeckJobRequest{
			AgentConfigID: req.AgentConfigID,
			Description:   req.Description,
			Mode:          req.Mode,
			SourceFiles:   normalizeSourceFiles(req),
		})
		if err != nil {
			return nil, err
		}
		return s.SubscribeJob(ctx, user.UserID, job.ID, 0)
	}
	if user.UserID <= 0 {
		return nil, fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.modelConfigRepo == nil {
		return nil, fmt.Errorf("model config repository is not configured")
	}
	if s.agentConfigRepo == nil {
		return nil, fmt.Errorf("agent config repository is not configured")
	}
	if s.historyRepo == nil {
		return nil, fmt.Errorf("ppt chat history repository is not configured")
	}
	if s.runtimeClient == nil {
		return nil, fmt.Errorf("runtime client is not configured")
	}

	description := strings.TrimSpace(req.Description)
	if description == "" {
		return nil, fmt.Errorf("%w: description is required", ErrInvalidRequest)
	}
	if len([]rune(description)) > 5000 {
		return nil, fmt.Errorf("%w: description must be <= 5000 characters", ErrInvalidRequest)
	}

	selectedConfig, err := s.resolvePPTAgentConfig(ctx, user.UserID, req.AgentConfigID)
	if err != nil {
		return nil, err
	}

	conversation, statusMessageID, err := s.startConversation(
		ctx,
		user.UserID,
		selectedConfig,
		description,
		sourceFileNames(req),
	)
	if err != nil {
		return nil, err
	}

	runtimeModels, err := s.runtimeModelsFromConfigByValue(ctx, user.UserID, selectedConfig)
	if err != nil {
		return nil, err
	}

	stream, err := s.runtimeClient.GenerateDeck(ctx, user, RuntimeGenerateDeckRequest{
		Description:      description,
		SourceFileName:   strings.TrimSpace(req.SourceFileName),
		SourceFile:       req.SourceFile,
		SourceFiles:      normalizeSourceFiles(req),
		NodeModelConfigs: runtimeModels,
	})
	if err != nil {
		return nil, err
	}

	return newPersistedDeckStream(ctx, s.historyRepo, conversation.ID, statusMessageID, stream), nil
}

func (s *Service) ResumeDeck(
	ctx context.Context,
	user actor.User,
	req ResumeDeckRequest,
) (DeckStream, error) {
	if s != nil && s.jobRepo != nil {
		requestID := strings.TrimSpace(req.RequestID)
		if requestID == "" {
			return nil, fmt.Errorf("%w: request_id is required", ErrInvalidRequest)
		}
		job, err := s.jobRepo.GetJobByRequestID(ctx, user.UserID, requestID)
		if err != nil {
			return nil, err
		}
		after := job.LastEventSequence
		_, _, err = s.SubmitJobAction(ctx, user, job.ID, DeckJobActionRequest{
			Action: req.Action, OutlineMarkdown: req.OutlineMarkdown, Payload: []byte(req.Payload),
		})
		if err != nil {
			return nil, err
		}
		return s.SubscribeJob(ctx, user.UserID, job.ID, after)
	}
	if s == nil {
		return nil, fmt.Errorf("ppt agent service is not configured")
	}
	if s.agentConfigRepo == nil {
		return nil, fmt.Errorf("agent config repository is not configured")
	}
	if s.modelConfigRepo == nil {
		return nil, fmt.Errorf("model config repository is not configured")
	}
	if s.historyRepo == nil {
		return nil, fmt.Errorf("ppt chat history repository is not configured")
	}
	if s.runtimeClient == nil {
		return nil, fmt.Errorf("runtime client is not configured")
	}

	requestID := strings.TrimSpace(req.RequestID)
	if requestID == "" {
		return nil, fmt.Errorf("%w: request_id is required", ErrInvalidRequest)
	}

	action := normalizeResumeAction(req.Action)
	outlineMarkdown := strings.TrimSpace(req.OutlineMarkdown)
	if action == "approve_outline" && outlineMarkdown == "" {
		return nil, fmt.Errorf("%w: outline_markdown is required", ErrInvalidRequest)
	}
	if len([]rune(outlineMarkdown)) > 50000 {
		return nil, fmt.Errorf("%w: outline_markdown must be <= 50000 characters", ErrInvalidRequest)
	}

	conversation, err := s.historyRepo.GetConversationByRequestID(ctx, user.UserID, requestID)
	if err != nil {
		return nil, err
	}

	selectedConfig, err := s.resolvePPTAgentConfig(ctx, user.UserID, conversation.AgentConfigID)
	if err != nil {
		return nil, err
	}
	if action == "approve_outline" {
		if err := s.markOutlineConfirmed(ctx, conversation.ID, requestID); err != nil {
			return nil, err
		}
	}
	if action == "choose_style" {
		if err := s.markStyleSelected(ctx, conversation.ID, requestID); err != nil {
			return nil, err
		}
	}
	statusMessageID, err := s.appendResumeMessages(ctx, conversation.ID, requestID, action)
	if err != nil {
		return nil, err
	}

	runtimeModels, err := s.runtimeModelsFromConfigByValue(ctx, user.UserID, selectedConfig)
	if err != nil {
		return nil, err
	}

	stream, err := s.runtimeClient.ResumeDeck(ctx, user, RuntimeResumeDeckRequest{
		RequestID:        requestID,
		OutlineMarkdown:  outlineMarkdown,
		Action:           action,
		PayloadJSON:      string(req.Payload),
		NodeModelConfigs: runtimeModels,
	})
	if err != nil {
		return nil, err
	}

	return newPersistedDeckStream(ctx, s.historyRepo, conversation.ID, statusMessageID, stream), nil
}

func normalizeSourceFiles(req GenerateDeckRequest) []SourceFile {
	sourceFiles := make([]SourceFile, 0, len(req.SourceFiles)+1)
	for _, file := range req.SourceFiles {
		fileName := strings.TrimSpace(file.FileName)
		if fileName == "" || len(file.Content) == 0 {
			continue
		}
		sourceFiles = append(sourceFiles, SourceFile{
			FileName: fileName,
			Content:  file.Content,
		})
	}

	if len(sourceFiles) == 0 && len(req.SourceFile) > 0 {
		fileName := strings.TrimSpace(req.SourceFileName)
		if fileName == "" {
			fileName = "uploaded-file"
		}
		sourceFiles = append(sourceFiles, SourceFile{
			FileName: fileName,
			Content:  req.SourceFile,
		})
	}
	return sourceFiles
}

func normalizeResumeAction(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "continue", "approve", "approved", "approve_outline":
		return "approve_outline"
	case "choose_style":
		return "choose_style"
	case "regenerate_slide":
		return "regenerate_slide"
	case "regenerate_outline":
		return "regenerate_outline"
	default:
		return strings.ToLower(strings.TrimSpace(value))
	}
}

func sourceFileNames(req GenerateDeckRequest) []string {
	sourceFiles := normalizeSourceFiles(req)
	names := make([]string, 0, len(sourceFiles))
	for _, file := range sourceFiles {
		names = append(names, file.FileName)
	}
	return names
}
