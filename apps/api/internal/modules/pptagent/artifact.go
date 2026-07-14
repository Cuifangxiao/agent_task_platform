package pptagent

import (
	"context"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var taskIDPattern = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

func (s *Service) OpenArtifact(_ context.Context, ownerID int64, taskID, relativePath string) (*os.File, error) {
	taskID = strings.ToLower(strings.TrimSpace(taskID))
	if !taskIDPattern.MatchString(taskID) {
		return nil, ErrArtifactNotFound
	}
	task, err := s.hub.Get(ownerID, taskID)
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
