package pptagent

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

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
	case "slides", "writer":
		return &persistedChunkSnapshot{
			Title: "正在展开页面",
			Text:  "我在把大纲扩展成每一页的具体内容。",
			State: string(ConversationStatusRunning),
		}
	case "design_slides", "designer":
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
	case "review", "review_vlm":
		return &persistedChunkSnapshot{
			Title: "正在检查内容",
			Text:  "我在检查逻辑、重复信息和内容完整性。",
			State: string(ConversationStatusRunning),
		}
	case "repair":
		return &persistedChunkSnapshot{
			Title: "正在自动修复",
			Text:  "我在保留原始内容的前提下修复页面质量问题。",
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
	case stage == "slides", stage == "writer":
		return "页面展开"
	case stage == "design_slides", stage == "designer":
		return "页面预览"
	case stage == "validate":
		return "质量校验"
	case stage == "coherence":
		return "连贯检查"
	case stage == "review", stage == "review_vlm":
		return "质量检查"
	case stage == "repair":
		return "自动修复"
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
