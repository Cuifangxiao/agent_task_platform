package user

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"strings"
	"time"

	"agent-task-platform/apps/api/internal/modules/auth"
	"golang.org/x/crypto/bcrypt"
)

var ErrInvalidCredentials = errors.New("username or password is incorrect")
var ErrUsernameAlreadyExists = errors.New("username already exists")
var ErrInvalidRefreshToken = errors.New("refresh token is invalid or expired")
var ErrSessionNotActive = errors.New("login session is invalid or expired")

type Service struct {
	repo         Repository
	tokenManager TokenManager
	sessionStore auth.SessionStore
}

type TokenManager interface {
	GenerateAccessToken(userID int64, username string, sessionID string, device string) (*auth.IssuedToken, error)
	GenerateRefreshToken(userID int64, username string, sessionID string, device string) (*auth.IssuedToken, error)
	ParseToken(tokenString string) (*auth.Claims, error)
	RefreshTokenTTL() time.Duration
}

func NewService(repo Repository, tokenManager TokenManager, sessionStore auth.SessionStore) *Service {
	return &Service{
		repo:         repo,
		tokenManager: tokenManager,
		sessionStore: sessionStore,
	}
}

func (s *Service) Login(ctx context.Context, req LoginRequest) (*AuthResponse, error) {
	username := strings.TrimSpace(req.Username)
	password := strings.TrimSpace(req.Password)
	device := normalizeDevice(req.Device)

	user, err := s.repo.GetByUsername(ctx, username)
	if err != nil {
		return nil, err
	}
	if user == nil {
		return nil, ErrInvalidCredentials
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(password)); err != nil {
		return nil, ErrInvalidCredentials
	}

	sessionID, err := newSessionID()
	if err != nil {
		return nil, err
	}

	resp, err := s.issueSession(ctx, user.ID, user.Username, sessionID, device, "login ok")
	if err != nil {
		return nil, err
	}

	return resp, nil
}

func (s *Service) Register(ctx context.Context, req RegisterRequest) (*RegisterResponse, error) {
	username := strings.TrimSpace(req.Username)
	password := strings.TrimSpace(req.Password)

	existingUser, err := s.repo.GetByUsername(ctx, username)
	if err != nil {
		return nil, err
	}
	if existingUser != nil {
		return nil, ErrUsernameAlreadyExists
	}

	passwordHash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return nil, err
	}

	err = s.repo.Create(ctx, User{
		Username:     username,
		PasswordHash: string(passwordHash),
	})
	if err != nil {
		return nil, err
	}

	return &RegisterResponse{
		Message: "register ok",
	}, nil
}

func (s *Service) Refresh(ctx context.Context, req RefreshRequest) (*AuthResponse, error) {
	refreshToken := strings.TrimSpace(req.RefreshToken)
	if refreshToken == "" {
		return nil, ErrInvalidRefreshToken
	}

	claims, err := s.tokenManager.ParseToken(refreshToken)
	if err != nil {
		return nil, ErrInvalidRefreshToken
	}
	if err := claims.RequireTokenType(auth.TokenTypeRefresh); err != nil {
		return nil, ErrInvalidRefreshToken
	}

	session, err := s.sessionStore.GetSession(ctx, claims.UserID, claims.Device)
	if err != nil {
		return nil, err
	}
	if !sessionMatchesClaims(session, claims) {
		return nil, ErrSessionNotActive
	}

	storedRefreshToken, err := s.sessionStore.GetRefreshToken(ctx, claims.UserID, claims.Device)
	if err != nil {
		return nil, err
	}
	if storedRefreshToken == "" || storedRefreshToken != refreshToken {
		return nil, ErrInvalidRefreshToken
	}

	resp, err := s.issueSession(ctx, claims.UserID, claims.Username, claims.SessionID, claims.Device, "refresh ok")
	if err != nil {
		return nil, err
	}

	return resp, nil
}

func (s *Service) Logout(ctx context.Context, claims *auth.Claims) error {
	if claims == nil {
		return ErrSessionNotActive
	}
	if err := claims.RequireTokenType(auth.TokenTypeAccess); err != nil {
		return ErrSessionNotActive
	}

	session, err := s.sessionStore.GetSession(ctx, claims.UserID, claims.Device)
	if err != nil {
		return err
	}
	if !sessionMatchesClaims(session, claims) {
		return ErrSessionNotActive
	}

	if err := s.sessionStore.DeleteSession(ctx, claims.UserID, claims.Device); err != nil {
		return err
	}

	return s.sessionStore.BlacklistAccessToken(ctx, claims.ID, time.Until(claims.ExpiresAt.Time))
}

func (s *Service) issueSession(ctx context.Context, userID int64, username string, sessionID string, device string, message string) (*AuthResponse, error) {
	accessToken, err := s.tokenManager.GenerateAccessToken(userID, username, sessionID, device)
	if err != nil {
		return nil, err
	}

	refreshToken, err := s.tokenManager.GenerateRefreshToken(userID, username, sessionID, device)
	if err != nil {
		return nil, err
	}

	session := auth.Session{
		UserID:    userID,
		Username:  username,
		SessionID: sessionID,
		Device:    device,
		Status:    auth.SessionStatusActive,
		CreatedAt: time.Now(),
		ExpiresAt: refreshToken.ExpiresAt,
	}
	if err := s.sessionStore.SaveSession(ctx, session, refreshToken.Token, s.tokenManager.RefreshTokenTTL()); err != nil {
		return nil, err
	}

	return &AuthResponse{
		Message:          message,
		Token:            accessToken.Token,
		AccessToken:      accessToken.Token,
		RefreshToken:     refreshToken.Token,
		TokenType:        "Bearer",
		Device:           device,
		SessionID:        sessionID,
		AccessExpiresAt:  accessToken.ExpiresAt,
		RefreshExpiresAt: refreshToken.ExpiresAt,
	}, nil
}

func normalizeDevice(device string) string {
	device = strings.TrimSpace(device)
	if device == "" {
		return "web"
	}

	if len(device) > 64 {
		return device[:64]
	}

	return device
}

func sessionMatchesClaims(session *auth.Session, claims *auth.Claims) bool {
	if session == nil || claims == nil {
		return false
	}

	return session.Status == auth.SessionStatusActive &&
		session.SessionID == claims.SessionID &&
		session.Device == claims.Device &&
		session.UserID == claims.UserID
}

func newSessionID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}

	return hex.EncodeToString(buf), nil
}
