package user

import (
	"context"
	"errors"
	"strings"

	"golang.org/x/crypto/bcrypt"
)

var ErrInvalidCredentials = errors.New("username or password is incorrect")
var ErrUsernameAlreadyExists = errors.New("username already exists")

type Service struct {
	repo         Repository
	tokenManager TokenManager
}

type TokenManager interface {
	GenerateToken(userID int64, username string) (string, error)
}

func NewService(repo Repository, tokenManager TokenManager) *Service {
	return &Service{
		repo:         repo,
		tokenManager: tokenManager,
	}
}

func (s *Service) Login(ctx context.Context, req LoginRequest) (*LoginResponse, error) {
	username := strings.TrimSpace(req.Username)
	password := strings.TrimSpace(req.Password)

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

	token, err := s.tokenManager.GenerateToken(user.ID, user.Username)
	if err != nil {
		return nil, err
	}

	return &LoginResponse{
		Message: "login ok",
		Token:   token,
	}, nil
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
