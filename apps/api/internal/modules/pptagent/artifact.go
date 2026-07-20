package pptagent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var taskIDPattern = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

func (s *Service) OpenArtifact(ctx context.Context, ownerID int64, taskID, relativePath string) (*os.File, error) {
	taskID = strings.ToLower(strings.TrimSpace(taskID))
	if !taskIDPattern.MatchString(taskID) {
		return nil, ErrArtifactNotFound
	}
	task, err := s.GetTask(ctx, ownerID, taskID)
	if err != nil {
		return nil, ErrArtifactNotFound
	}
	if strings.TrimSpace(relativePath) == "" {
		if task.Result == nil || task.Result.PPTFile.RelativePath == "" {
			return nil, ErrArtifactNotFound
		}
		relativePath = task.Result.PPTFile.RelativePath
	}

	canonical, err := normalizeArtifactRelativePath(relativePath)
	if err != nil {
		return nil, ErrArtifactNotFound
	}
	rootPath, err := filepath.Abs(s.artifactDir)
	if err != nil {
		return nil, ErrArtifactNotFound
	}
	root, err := os.OpenRoot(rootPath)
	if err != nil {
		return nil, ErrArtifactNotFound
	}
	defer root.Close()
	file, err := root.Open(filepath.Join(taskID, filepath.FromSlash(canonical)))
	if err != nil {
		return nil, ErrArtifactNotFound
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, ErrArtifactNotFound
	}
	if !info.Mode().IsRegular() {
		file.Close()
		return nil, ErrArtifactNotFound
	}
	return file, nil
}

// verifyArtifact prevents an ARTIFACT_READY or succeeded snapshot from being
// committed before the advertised file is actually available from the same
// artifact root used by the download endpoint.
func (s *Service) verifyArtifact(runID string, resource ResourceRef) error {
	runID = normalizeTaskID(runID)
	if !taskIDPattern.MatchString(runID) {
		return fmt.Errorf("invalid artifact run id")
	}
	relativePath, err := normalizeArtifactRelativePath(resource.RelativePath)
	if err != nil {
		return err
	}
	root, err := os.OpenRoot(s.artifactDir)
	if err != nil {
		return fmt.Errorf("open artifact root: %w", err)
	}
	defer root.Close()
	file, err := root.Open(filepath.Join(runID, filepath.FromSlash(relativePath)))
	if err != nil {
		return fmt.Errorf("open artifact: %w", err)
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return fmt.Errorf("stat artifact: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() <= 0 {
		return fmt.Errorf("artifact is not a non-empty regular file")
	}
	if resource.SizeBytes > 0 && uint64(info.Size()) != resource.SizeBytes {
		return fmt.Errorf("artifact size does not match runtime metadata")
	}
	expectedDigest := strings.ToLower(strings.TrimSpace(resource.SHA256))
	if expectedDigest == "" {
		return nil
	}
	decoded, err := hex.DecodeString(expectedDigest)
	if err != nil || len(decoded) != sha256.Size {
		return fmt.Errorf("artifact sha256 metadata is invalid")
	}
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return fmt.Errorf("hash artifact: %w", err)
	}
	if !strings.EqualFold(hex.EncodeToString(digest.Sum(nil)), expectedDigest) {
		return fmt.Errorf("artifact sha256 does not match runtime metadata")
	}
	return nil
}
