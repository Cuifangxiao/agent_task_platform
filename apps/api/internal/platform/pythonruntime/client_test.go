package pythonruntime

import (
	"io"
	"testing"

	pptagentpb "agent-task-platform/contracts/gen/go/pptagent"
)

type oneEventPPTStream struct {
	event *pptagentpb.AgentEvent
}

func (stream *oneEventPPTStream) Recv() (*pptagentpb.AgentEvent, error) {
	if stream.event == nil {
		return nil, io.EOF
	}
	event := stream.event
	stream.event = nil
	return event, nil
}

func TestPPTStreamPreservesProducerEventID(t *testing.T) {
	want := "slide-preview:run-1:s01:0123456789abcdef0123"
	wantRevision := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	stream := &pptStream{stream: &oneEventPPTStream{event: &pptagentpb.AgentEvent{
		TaskId:   "run-1",
		EventId:  want,
		Revision: wantRevision,
		Sequence: 3,
		Type:     pptagentpb.EventType_ARTIFACT_READY,
		Stage:    pptagentpb.PptStage_RENDERING,
		Message:  "preview ready",
	}}}

	event, err := stream.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if event.EventID != want {
		t.Fatalf("event id = %q, want %q", event.EventID, want)
	}
	if event.Revision != wantRevision {
		t.Fatalf("revision = %q, want %q", event.Revision, wantRevision)
	}
}
