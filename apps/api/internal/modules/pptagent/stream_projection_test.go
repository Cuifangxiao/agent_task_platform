package pptagent

import "testing"

func TestDesignLockStageHasPersistedPresentationCopy(t *testing.T) {
	chunk := &DeckStreamChunk{Stage: "design_lock", EventType: "stage_progress"}
	snapshot := describePersistedChunk(chunk)
	if snapshot == nil {
		t.Fatal("design_lock stage should produce a persisted snapshot")
	}
	if snapshot.State != string(ConversationStatusRunning) {
		t.Fatalf("expected running snapshot, got %q", snapshot.State)
	}
	if label := stageLabelForPersistence("design_lock", "stage_progress"); label != "设计规范锁定" {
		t.Fatalf("unexpected design_lock label: %q", label)
	}
}
