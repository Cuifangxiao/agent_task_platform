package pptagent

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func (s *Service) ArtifactPath(requestID string) (string, error) {
	return s.ArtifactAssetPath(requestID, "")
}

func (s *Service) ArtifactAssetPathForUser(ctx context.Context, userID int64, requestID string, relativePath string) (string, error) {
	if userID <= 0 {
		return "", fmt.Errorf("%w: invalid user id", ErrInvalidRequest)
	}
	if s.jobRepo == nil {
		return "", ErrArtifactNotFound
	}
	if _, err := s.jobRepo.GetJobByRequestID(ctx, userID, strings.TrimSpace(requestID)); err != nil {
		if errors.Is(err, ErrDeckJobNotFound) {
			return "", ErrArtifactNotFound
		}
		return "", err
	}
	path, err := s.ArtifactAssetPath(requestID, relativePath)
	if err != nil {
		return "", err
	}
	if strings.HasSuffix(strings.ToLower(path), ".html") {
		data, readErr := os.ReadFile(path)
		if readErr != nil {
			return "", readErr
		}
		if unsafeHTMLArtifactPattern.Match(data) {
			return "", ErrArtifactNotFound
		}
	}
	return path, nil
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
		path = filepath.Join(cleanDir, requestID, "exports", "deck.pptx")
		if _, err := os.Stat(path); err != nil && os.IsNotExist(err) {
			path = filepath.Join(cleanDir, requestID, "deck.pptx")
			if _, legacyErr := os.Stat(path); legacyErr != nil && os.IsNotExist(legacyErr) {
				path = filepath.Join(cleanDir, requestID+".pptx")
			}
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
