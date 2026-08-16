package pptagent

import (
	"strings"
	"testing"
)

func TestNormalizePresentationNameUsesExplicitName(t *testing.T) {
	name, err := normalizePresentationName("  Transformer   模型介绍  ", "ignored")
	if err != nil || name != "Transformer 模型介绍" {
		t.Fatalf("name=%q err=%v", name, err)
	}
}

func TestNormalizePresentationNameDerivesReadableFallback(t *testing.T) {
	name, err := normalizePresentationName("", "生成一份面向初级工程师的 Transformer 培训课件，深色科技风格。")
	if err != nil || name != "面向初级工程师的 Transformer 培训课件" {
		t.Fatalf("name=%q err=%v", name, err)
	}
}

func TestPresentationDownloadNameKeepsUnicodeAndRemovesUnsafeCharacters(t *testing.T) {
	name := presentationDownloadName(`Transformer: 原理/应用?`, "")
	if name != "Transformer- 原理-应用.pptx" {
		t.Fatalf("download name=%q", name)
	}
}

func TestNormalizePresentationNameRejectsLongExplicitName(t *testing.T) {
	_, err := normalizePresentationName(strings.Repeat("名", maxPresentationNameRunes+1), "description")
	if err == nil {
		t.Fatal("expected long name to be rejected")
	}
}
