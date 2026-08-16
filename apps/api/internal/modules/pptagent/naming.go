package pptagent

import (
	"fmt"
	"strings"
	"unicode"
)

const maxPresentationNameRunes = 120

var presentationNamePrefixes = []string{
	"请帮我制作一份", "请帮我生成一份", "帮我制作一份", "帮我生成一份",
	"请制作一份", "请生成一份", "制作一份", "生成一份", "做一份",
	"请帮我制作", "请帮我生成", "帮我制作", "帮我生成", "请制作", "请生成", "制作", "生成",
}

func normalizePresentationName(raw, description string) (string, error) {
	name := collapsePresentationWhitespace(raw)
	if len([]rune(name)) > maxPresentationNameRunes {
		return "", fmt.Errorf("%w: presentation name must not exceed %d characters", ErrInvalidRequest, maxPresentationNameRunes)
	}
	if name != "" {
		return name, nil
	}
	name = derivePresentationName(description)
	if name == "" {
		name = "演示文稿"
	}
	return truncateRunes(name, maxPresentationNameRunes), nil
}

func derivePresentationName(description string) string {
	name := collapsePresentationWhitespace(description)
	if index := strings.IndexAny(name, "，,。！？!?；;：:\n\r"); index >= 0 {
		name = strings.TrimSpace(name[:index])
	}
	for _, prefix := range presentationNamePrefixes {
		if strings.HasPrefix(name, prefix) {
			name = strings.TrimSpace(strings.TrimPrefix(name, prefix))
			break
		}
	}
	name = strings.Trim(name, " .-_—：:，,。！？!?")
	return truncateRunes(name, 48)
}

func presentationDownloadName(name, description string) string {
	normalized, _ := normalizePresentationName(name, description)
	var builder strings.Builder
	for _, value := range normalized {
		switch {
		case value < 32 || value == 127:
			continue
		case strings.ContainsRune(`<>:"/\\|?*`, value):
			builder.WriteRune('-')
		default:
			builder.WriteRune(value)
		}
	}
	base := strings.Trim(builder.String(), " .-")
	if base == "" {
		base = "演示文稿"
	}
	return truncateRunes(base, maxPresentationNameRunes) + ".pptx"
}

func collapsePresentationWhitespace(value string) string {
	return strings.Join(strings.FieldsFunc(strings.TrimSpace(value), unicode.IsSpace), " ")
}

func truncateRunes(value string, limit int) string {
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return strings.TrimSpace(string(runes[:limit]))
}
