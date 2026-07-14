package pptagent

import (
	"encoding/json"
	"testing"
)

func TestApplyValidationIssuesReplacesResolvedWarnings(t *testing.T) {
	stream := &persistedDeckStream{
		statusContent: persistedStatusContent{
			Slides: []persistedSlideProgress{{Index: 1, Status: "ready"}},
		},
	}

	stream.applyValidationIssues(json.RawMessage(`{
		"issues":[{"page":1,"severity":"must_fix","description":"BERT and GPT need a source."}]
	}`))
	if got := stream.statusContent.Slides[0]; got.Status != "warning" || len(got.Warnings) != 1 {
		t.Fatalf("first validation slide = %#v", got)
	}

	stream.applyValidationIssues(json.RawMessage(`{
		"issues":[{"page":1,"severity":"must_fix","description":"Current overflow warning."}]
	}`))
	got := stream.statusContent.Slides[0]
	if got.Status != "warning" || len(got.Warnings) != 1 || got.Warnings[0] != "Current overflow warning." {
		t.Fatalf("replacement validation slide = %#v", got)
	}

	stream.applyValidationIssues(json.RawMessage(`{"issues":[]}`))
	got = stream.statusContent.Slides[0]
	if got.Status != "reviewed" || len(got.Warnings) != 0 {
		t.Fatalf("resolved validation slide = %#v", got)
	}
}
