package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

var ErrInvalidRequest = errors.New("invalid ppt agent request")
var ErrAgentConfigNotFound = errors.New("agent config not found")
var ErrModelConfigNotFound = errors.New("model config not found")
var ErrModelConfigInactive = errors.New("model config is inactive")
var ErrArtifactNotFound = errors.New("deck artifact not found")

var artifactIDPattern = regexp.MustCompile(`^[a-fA-F0-9-]{36}$`)

type Service struct {
	modelConfigRepo modelconfig.Repository
	agentConfigRepo agentconfig.Repository
	historyRepo     HistoryRepository
	runtimeClient   RuntimeClient
	artifactDir     string
}

func NewService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	historyRepo HistoryRepository,
	runtimeClient RuntimeClient,
	artifactDir string,
) *Service {
	if strings.TrimSpace(artifactDir) == "" {
		artifactDir = filepath.Join("python-agent", "artifacts", "decks")
	}
	if abs, err := filepath.Abs(artifactDir); err == nil {
		artifactDir = abs
	}

	return &Service{
		modelConfigRepo: modelConfigRepo,
		agentConfigRepo: agentConfigRepo,
		historyRepo:     historyRepo,
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
	user providercredential.UserContext,
	req ResumeDeckRequest,
) (DeckStream, error) {
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

func (s *Service) ArtifactPath(requestID string) (string, error) {
	return s.ArtifactAssetPath(requestID, "")
}

func (s *Service) ArtifactAssetPath(requestID string, relativePath string) (string, error) {
	requestID = strings.TrimSpace(requestID)
	if !artifactIDPattern.MatchString(requestID) {
		return "", fmt.Errorf("%w: invalid request id", ErrInvalidRequest)
	}

	cleanDir := filepath.Clean(s.artifactDir)
	var path string
	relativePath = strings.TrimPrefix(strings.TrimSpace(relativePath), "/")
	if relativePath == "" {
		path = filepath.Join(cleanDir, requestID, "deck.pptx")
		if _, err := os.Stat(path); err != nil && os.IsNotExist(err) {
			path = filepath.Join(cleanDir, requestID+".pptx")
		}
	} else {
		relClean := filepath.Clean(relativePath)
		if relClean == "." || strings.HasPrefix(relClean, "..") || filepath.IsAbs(relClean) {
			return "", fmt.Errorf("%w: invalid artifact path", ErrInvalidRequest)
		}
		path = filepath.Join(cleanDir, requestID, relClean)
	}
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

func (s *Service) resolveRuntimeModelConfigs(
	ctx context.Context,
	userID int64,
	agentConfigID int64,
) ([]RuntimeNodeModelConfig, error) {
	config, err := s.resolvePPTAgentConfig(ctx, userID, agentConfigID)
	if err != nil {
		return nil, err
	}
	return s.runtimeModelsFromConfigByValue(ctx, userID, config)
}

func (s *Service) runtimeModelsFromConfigByValue(
	ctx context.Context,
	userID int64,
	config *agentconfig.AgentConfig,
) ([]RuntimeNodeModelConfig, error) {
	if config == nil {
		return nil, fmt.Errorf("%w: agent config is required", ErrInvalidRequest)
	}
	agent, err := s.agentConfigRepo.GetAgentByID(ctx, config.AgentID)
	if err != nil {
		return nil, err
	}
	return s.runtimeModelsFromConfig(ctx, userID, config, agent)
}

func (s *Service) resolvePPTAgentConfig(
	ctx context.Context,
	userID int64,
	agentConfigID int64,
) (*agentconfig.AgentConfig, error) {
	if agentConfigID > 0 {
		config, err := s.agentConfigRepo.GetConfigByID(ctx, userID, agentConfigID)
		if err != nil {
			if errors.Is(err, agentconfig.ErrNotFound) {
				return nil, ErrAgentConfigNotFound
			}
			return nil, err
		}
		if config.AgentKey != agentconfig.AgentKeyPPT {
			return nil, fmt.Errorf("%w: agent config is not for ppt_agent", ErrInvalidRequest)
		}
		if config.Status != agentconfig.StatusActive {
			return nil, fmt.Errorf("%w: agent config is inactive", ErrInvalidRequest)
		}
		return config, nil
	}

	configs, err := s.agentConfigRepo.ListConfigsByUserID(ctx, userID, 0)
	if err != nil {
		return nil, err
	}

	var fallback *agentconfig.AgentConfig
	for _, config := range configs {
		if config == nil || config.AgentKey != agentconfig.AgentKeyPPT || config.Status != agentconfig.StatusActive {
			continue
		}
		if config.IsDefault {
			return config, nil
		}
		if fallback == nil {
			fallback = config
		}
	}
	if fallback != nil {
		return fallback, nil
	}

	return nil, ErrAgentConfigNotFound
}

func (s *Service) runtimeModelsFromConfig(
	ctx context.Context,
	userID int64,
	agentConfig *agentconfig.AgentConfig,
	agent *agentconfig.AgentDefinition,
) ([]RuntimeNodeModelConfig, error) {
	requiredNodes := modelNodeKeys(agent.Nodes)
	byNode := make(map[string]agentconfig.NodeBinding, len(agentConfig.NodeBindings))
	for _, binding := range agentConfig.NodeBindings {
		byNode[binding.NodeKey] = binding
	}

	runtimeModels := make([]RuntimeNodeModelConfig, 0, len(requiredNodes))
	for _, nodeKey := range requiredNodes {
		binding, ok := byNode[nodeKey]
		if !ok {
			fallbackKey := legacyModelNodeFallback(nodeKey)
			if fallbackKey != "" {
				binding, ok = byNode[fallbackKey]
			}
		}
		if !ok {
			return nil, fmt.Errorf("%w: agent config missing node binding '%s'", ErrInvalidRequest, nodeKey)
		}

		config, err := s.modelConfigRepo.GetByID(ctx, userID, binding.ModelConfigID)
		if err != nil {
			if errors.Is(err, modelconfig.ErrNotFound) {
				return nil, fmt.Errorf("%w: model config for node '%s' was not found", ErrModelConfigNotFound, nodeKey)
			}
			return nil, err
		}
		if config.Status != modelconfig.StatusActive {
			return nil, fmt.Errorf("%w: model config for node '%s' is inactive", ErrModelConfigInactive, nodeKey)
		}

		temperature := config.Temperature
		if binding.Temperature != nil {
			temperature = *binding.Temperature
		}
		maxTokens := config.MaxTokens
		if binding.MaxTokens != nil {
			maxTokens = *binding.MaxTokens
		}

		runtimeModels = append(runtimeModels, RuntimeNodeModelConfig{
			NodeKey:             nodeKey,
			Provider:            config.Provider,
			Model:               config.ModelName,
			APIKey:              config.APIKey,
			BaseURL:             config.BaseURL,
			ProviderOptionsJSON: normalizedProviderOptions(config.ProviderOptions),
			Temperature:         temperature,
			MaxTokens:           maxTokens,
		})
	}

	return runtimeModels, nil
}

func legacyModelNodeFallback(nodeKey string) string {
	switch strings.ToLower(strings.TrimSpace(nodeKey)) {
	case "brief":
		return "outline"
	case "style", "writer", "designer", "coherence":
		return "slides"
	case "repair":
		return "designer"
	default:
		return ""
	}
}

func modelNodeKeys(nodes []agentconfig.NodeDefinition) []string {
	keys := make([]string, 0, len(nodes))
	for _, node := range nodes {
		if node.RequiresModel {
			keys = append(keys, node.Key)
		}
	}
	return keys
}

func normalizedProviderOptions(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "{}"
	}
	return value
}

type persistedProgressStep struct {
	ID      int64  `json:"id"`
	Label   string `json:"label"`
	Message string `json:"message"`
	Tone    string `json:"tone"`
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
	}
	s.statusContent.Steps = append(s.statusContent.Steps, step)
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
		!(chunk.EventType == "stage_progress" && chunk.Stage == "slides") {
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
		PreviewThumb:     payloadString(payload["thumb_b64"]),
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
	content := map[string]any{
		"title":     "PPT 已准备好",
		"text":      "整套内容已经生成完成，你可以直接下载这份 PPT。",
		"requestId": requestID,
		"fileName":  fileName,
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

type persistedChunkSnapshot struct {
	Title string
	Text  string
	State string
}

func describePersistedChunk(chunk *DeckStreamChunk) *persistedChunkSnapshot {
	if chunk == nil {
		return nil
	}
	if chunk.Error != "" {
		return &persistedChunkSnapshot{
			Title: "生成失败",
			Text:  chunk.Error,
			State: string(ConversationStatusError),
		}
	}
	if chunk.EventType == "interrupt" {
		if chunk.Stage == "style_review" {
			return &persistedChunkSnapshot{
				Title: "等待选择风格",
				Text:  "两套视觉方向已经准备好，选择后继续生成页面。",
				State: string(ConversationStatusWaiting),
			}
		}
		return &persistedChunkSnapshot{
			Title: "等待确认大纲",
			Text:  "我已经整理好一版大纲，确认后就继续生成完整 PPT。",
			State: string(ConversationStatusWaiting),
		}
	}
	if chunk.EventType == "artifact" || chunk.EventType == "done" {
		return &persistedChunkSnapshot{
			Title: "PPT 已生成",
			Text:  "PPT 已经生成完成，可以直接下载。",
			State: string(ConversationStatusDone),
		}
	}

	switch chunk.Stage {
	case "brief":
		return &persistedChunkSnapshot{
			Title: "正在理解需求",
			Text:  "我在整理你的目标和上下文信息。",
			State: string(ConversationStatusRunning),
		}
	case "outline":
		return &persistedChunkSnapshot{
			Title: "正在规划大纲",
			Text:  "我在梳理 PPT 结构和每一页的重点。",
			State: string(ConversationStatusRunning),
		}
	case "outline_review":
		return &persistedChunkSnapshot{
			Title: "等待确认大纲",
			Text:  "大纲已经准备好，确认后我继续生成页面内容。",
			State: string(ConversationStatusRunning),
		}
	case "style":
		return &persistedChunkSnapshot{
			Title: "正在生成风格样张",
			Text:  "我在准备两套视觉方向。",
			State: string(ConversationStatusRunning),
		}
	case "style_review":
		return &persistedChunkSnapshot{
			Title: "等待选择风格",
			Text:  "请选择一套视觉方向继续。",
			State: string(ConversationStatusRunning),
		}
	case "slides":
		return &persistedChunkSnapshot{
			Title: "正在展开页面",
			Text:  "我在把大纲扩展成每一页的具体内容。",
			State: string(ConversationStatusRunning),
		}
	case "design_slides":
		return &persistedChunkSnapshot{
			Title: "正在生成页面预览",
			Text:  "我在把结构化内容渲染成 HTML 与 PNG 预览。",
			State: string(ConversationStatusRunning),
		}
	case "validate":
		return &persistedChunkSnapshot{
			Title: "正在校验页面",
			Text:  "我在检查占位、密度和页面结构。",
			State: string(ConversationStatusRunning),
		}
	case "coherence":
		return &persistedChunkSnapshot{
			Title: "正在统一全局表达",
			Text:  "我在检查跨页连贯性。",
			State: string(ConversationStatusRunning),
		}
	case "review":
		return &persistedChunkSnapshot{
			Title: "正在检查内容",
			Text:  "我在检查逻辑、重复信息和内容完整性。",
			State: string(ConversationStatusRunning),
		}
	case "render", "export":
		return &persistedChunkSnapshot{
			Title: "正在导出 PPT",
			Text:  "正在把内容渲染成可下载的 PPT 文件。",
			State: string(ConversationStatusRunning),
		}
	default:
		return nil
	}
}

func stageLabelForPersistence(stage string, eventType string) string {
	switch {
	case stage == "brief":
		return "需求理解"
	case stage == "outline":
		return "大纲规划"
	case stage == "outline_review":
		return "大纲确认"
	case stage == "style":
		return "风格样张"
	case stage == "style_review":
		return "风格选择"
	case stage == "slides":
		return "页面展开"
	case stage == "design_slides":
		return "页面预览"
	case stage == "validate":
		return "质量校验"
	case stage == "coherence":
		return "连贯检查"
	case stage == "review":
		return "质量检查"
	case stage == "render", stage == "export":
		return "PPT 导出"
	case eventType == "artifact":
		return "下载结果"
	case eventType == "done":
		return "完成"
	case eventType == "error":
		return "错误"
	default:
		return "处理中"
	}
}

func stepMessageForPersistence(chunk *DeckStreamChunk) string {
	if chunk == nil {
		return ""
	}
	if chunk.Error != "" {
		return chunk.Error
	}
	if strings.TrimSpace(chunk.Message) != "" {
		return chunk.Message
	}
	return "已收到新的生成事件。"
}

func toneForPersistence(chunk *DeckStreamChunk) string {
	if chunk == nil {
		return "normal"
	}
	if chunk.Error != "" {
		return "error"
	}
	if chunk.Done {
		return "done"
	}
	return "normal"
}

func payloadString(value any) string {
	text, ok := value.(string)
	if !ok {
		return ""
	}
	return strings.TrimSpace(text)
}

func payloadStringSlice(value any) []string {
	items, ok := value.([]any)
	if !ok {
		return nil
	}

	result := make([]string, 0, len(items))
	for _, item := range items {
		text := strings.TrimSpace(fmt.Sprint(item))
		if text != "" {
			result = append(result, text)
		}
	}
	return result
}

func payloadInt(value any) int {
	switch typed := value.(type) {
	case float64:
		return int(typed)
	case int:
		return typed
	case string:
		parsed, err := strconv.Atoi(strings.TrimSpace(typed))
		if err == nil {
			return parsed
		}
	}
	return 0
}

func normalizePersistedSlideStatus(value string) string {
	switch strings.TrimSpace(value) {
	case "pending", "running", "ready", "reviewed", "warning", "error":
		return strings.TrimSpace(value)
	case "ok":
		return "reviewed"
	default:
		return ""
	}
}

func appendUniqueStrings(existing []string, values ...string) []string {
	seen := make(map[string]struct{}, len(existing)+len(values))
	result := make([]string, 0, len(existing)+len(values))
	for _, value := range existing {
		text := strings.TrimSpace(value)
		if text == "" {
			continue
		}
		if _, ok := seen[text]; ok {
			continue
		}
		seen[text] = struct{}{}
		result = append(result, text)
	}
	for _, value := range values {
		text := strings.TrimSpace(value)
		if text == "" {
			continue
		}
		if _, ok := seen[text]; ok {
			continue
		}
		seen[text] = struct{}{}
		result = append(result, text)
	}
	return result
}

func extractOutlineMarkdownForPersistence(chunk *DeckStreamChunk) string {
	if chunk == nil {
		return ""
	}
	if strings.TrimSpace(chunk.OutlineMarkdown) != "" {
		return chunk.OutlineMarkdown
	}
	if strings.TrimSpace(chunk.Content) != "" {
		return chunk.Content
	}
	raw := chunk.Payload
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return ""
	}
	if outline, ok := payload["outline_markdown"].(string); ok {
		return outline
	}
	if outline, ok := payload["outlineMarkdown"].(string); ok {
		return outline
	}
	if outline, ok := payload["outline"].(string); ok {
		return outline
	}
	return ""
}

func extractStyleOptionsForPersistence(chunk *DeckStreamChunk) []any {
	if chunk == nil || len(chunk.Payload) == 0 {
		return nil
	}
	var payload map[string]any
	if err := json.Unmarshal(chunk.Payload, &payload); err != nil {
		return nil
	}
	if options, ok := payload["options"].([]any); ok {
		return options
	}
	if options, ok := payload["style_options"].([]any); ok {
		return options
	}
	return nil
}

func extractArtifactFileNameForPersistence(raw json.RawMessage) string {
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return ""
	}
	if fileName, ok := payload["filename"].(string); ok {
		return fileName
	}
	return ""
}

func mustMarshalJSON(value any) string {
	data, err := json.Marshal(value)
	if err != nil {
		return "{}"
	}
	return string(data)
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
	default:
		return "approve_outline"
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
