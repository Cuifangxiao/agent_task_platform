package pptagent

import (
	"fmt"
	"math"
	"path"
	"regexp"
	"strings"
)

var windowsVolumePattern = regexp.MustCompile(`^[A-Za-z]:`)
var artifactRevisionPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

const maxEventIDBytes = 160

func validateAgentEvent(event AgentEvent) error {
	if len(event.EventID) > maxEventIDBytes {
		return fmt.Errorf("event_id exceeds %d bytes", maxEventIDBytes)
	}
	for _, value := range []byte(event.EventID) {
		if value < 0x21 || value > 0x7e {
			return fmt.Errorf("event_id must contain printable ASCII without whitespace")
		}
	}
	if event.Revision != "" && !artifactRevisionPattern.MatchString(event.Revision) {
		return fmt.Errorf("revision must be a lowercase SHA-256 digest")
	}
	if event.Sequence == 0 {
		return fmt.Errorf("event sequence is required")
	}
	if !validEventType(event.Type) {
		return fmt.Errorf("unknown event type %q", event.Type)
	}
	if !validStage(event.Stage) {
		return fmt.Errorf("unknown stage %q", event.Stage)
	}
	if strings.TrimSpace(event.Message) == "" {
		return fmt.Errorf("event message is required")
	}
	if event.Progress != nil {
		percent := float64(event.Progress.Percent)
		if math.IsNaN(percent) || math.IsInf(percent, 0) || percent < 0 || percent > 100 {
			return fmt.Errorf("progress percent must be between 0 and 100")
		}
		if event.Progress.Current > event.Progress.Total {
			return fmt.Errorf("progress current exceeds total")
		}
	}
	if event.Page != nil {
		if event.Page.PageIndex == 0 {
			return fmt.Errorf("page_index must be positive")
		}
		if event.Page.PageCount > 0 && event.Page.PageIndex > event.Page.PageCount {
			return fmt.Errorf("page_index exceeds page_count")
		}
	}
	for _, resource := range event.Resources {
		if err := validateResource(resource); err != nil {
			return err
		}
	}

	switch event.Type {
	case EventTaskCompleted:
		if event.Stage != StageFinished {
			return fmt.Errorf("completed event must use FINISHED stage")
		}
		if event.FinalResult == nil {
			return fmt.Errorf("completed event requires final_result")
		}
		if event.Error != nil {
			return fmt.Errorf("completed event cannot include an error")
		}
		if event.FinalResult.PPTFile.Type != ResourcePPTX {
			return fmt.Errorf("final_result must reference a PPTX resource")
		}
		if err := validateResource(event.FinalResult.PPTFile); err != nil {
			return fmt.Errorf("invalid final_result: %w", err)
		}
	case EventTaskFailed:
		if event.Error == nil || strings.TrimSpace(event.Error.Code) == "" || strings.TrimSpace(event.Error.Message) == "" {
			return fmt.Errorf("failed event requires a structured error")
		}
		if event.FinalResult != nil {
			return fmt.Errorf("failed event cannot include final_result")
		}
	default:
		if event.Error != nil || event.FinalResult != nil {
			return fmt.Errorf("non-terminal event cannot include terminal details")
		}
	}
	if event.Type == EventArtifactReady && len(event.Resources) == 0 {
		return fmt.Errorf("artifact event requires at least one resource")
	}
	return nil
}

func validateResource(resource ResourceRef) error {
	if strings.TrimSpace(resource.ResourceID) == "" {
		return fmt.Errorf("resource_id is required")
	}
	if !validResourceType(resource.Type) {
		return fmt.Errorf("unknown resource type %q", resource.Type)
	}
	if strings.TrimSpace(resource.Name) == "" {
		return fmt.Errorf("resource name is required")
	}
	if _, err := normalizeArtifactRelativePath(resource.RelativePath); err != nil {
		return err
	}
	return nil
}

func normalizeArtifactRelativePath(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if strings.Contains(value, `\`) {
		return "", fmt.Errorf("artifact relative_path must use POSIX separators")
	}
	if value == "" || strings.HasPrefix(value, "/") || windowsVolumePattern.MatchString(value) || strings.ContainsRune(value, '\x00') {
		return "", fmt.Errorf("artifact relative_path is invalid")
	}
	for _, part := range strings.Split(value, "/") {
		if part == "" || part == "." || part == ".." {
			return "", fmt.Errorf("artifact relative_path escapes the task directory")
		}
	}
	clean := path.Clean(value)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return "", fmt.Errorf("artifact relative_path is invalid")
	}
	return clean, nil
}

func validEventType(value EventType) bool {
	switch value {
	case EventTaskStarted, EventStepStarted, EventStepProgress, EventStepCompleted, EventArtifactReady, EventTaskCompleted, EventTaskFailed:
		return true
	default:
		return false
	}
}

func validStage(value Stage) bool {
	switch value {
	case StagePreparing, StagePlanning, StageGeneratingContent, StageGeneratingAssets, StageGeneratingPages, StageRendering, StageAssemblingPPT, StageFinished:
		return true
	default:
		return false
	}
}

func validResourceType(value ResourceType) bool {
	switch value {
	case ResourceInputAttachment, ResourceImage, ResourceSVG, ResourceHTML, ResourcePagePreview, ResourcePPTX, ResourceOtherFile:
		return true
	default:
		return false
	}
}
