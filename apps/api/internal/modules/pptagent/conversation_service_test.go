package pptagent

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestDeleteConversationRemovesValidatedArtifactDirectories(t *testing.T) {
	artifactRoot := t.TempDir()
	requestID := "11111111-1111-1111-1111-111111111111"
	artifactPath := filepath.Join(artifactRoot, requestID)
	if err := os.MkdirAll(artifactPath, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(artifactPath, "deck.pptx"), []byte("deck"), 0o600); err != nil {
		t.Fatal(err)
	}
	unrelatedPath := filepath.Join(artifactRoot, "keep.txt")
	if err := os.WriteFile(unrelatedPath, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}

	repo := &fakeHistoryRepo{deleteRequestIDs: []string{requestID, "../keep.txt"}}
	service := NewService(nil, nil, repo, nil, artifactRoot)
	if err := service.DeleteConversation(context.Background(), 7, 30); err != nil {
		t.Fatalf("DeleteConversation returned error: %v", err)
	}
	if repo.deletedConversationID != 30 {
		t.Fatalf("deleted conversation id = %d, want 30", repo.deletedConversationID)
	}
	if _, err := os.Stat(artifactPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("artifact directory still exists or stat failed unexpectedly: %v", err)
	}
	if _, err := os.Stat(unrelatedPath); err != nil {
		t.Fatalf("unrelated file was removed: %v", err)
	}
}

func TestDeleteConversationValidatesIdentifiers(t *testing.T) {
	service := NewService(nil, nil, &fakeHistoryRepo{}, nil, t.TempDir())
	for _, testCase := range []struct {
		name   string
		userID int64
		id     int64
	}{
		{name: "invalid user", userID: 0, id: 1},
		{name: "invalid conversation", userID: 7, id: 0},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			err := service.DeleteConversation(context.Background(), testCase.userID, testCase.id)
			if !errors.Is(err, ErrInvalidRequest) {
				t.Fatalf("DeleteConversation error = %v, want ErrInvalidRequest", err)
			}
		})
	}
}

func TestConversationDeletionOnlyBlocksExecutingJobs(t *testing.T) {
	for _, testCase := range []struct {
		status DeckJobStatus
		want   bool
	}{
		{status: DeckJobQueued, want: true},
		{status: DeckJobRunning, want: true},
		{status: DeckJobExporting, want: true},
		{status: DeckJobWaitingForOutline, want: false},
		{status: DeckJobWaitingForStyle, want: false},
		{status: DeckJobSuccess, want: false},
		{status: DeckJobFailed, want: false},
	} {
		if got := conversationDeletionBlocked(testCase.status); got != testCase.want {
			t.Errorf("conversationDeletionBlocked(%q) = %v, want %v", testCase.status, got, testCase.want)
		}
	}
}
