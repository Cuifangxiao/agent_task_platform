"""Text-layer JavaScript and geometry constants shared by render/export code."""

TEXT_LAYER_JS = """
() => Array.from(document.querySelectorAll('[data-text]')).map((el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const declaredBox = (el.dataset.box || '').split(',').map((item) => Number.parseFloat(item));
  const hasDeclaredBox = declaredBox.length === 4 && declaredBox.every((item) => Number.isFinite(item));
  const sourceText = el.dataset.value || el.innerText || el.textContent || '';
  const fontSize = parseFloat(el.dataset.fontSize || style.fontSize || '18');
  const lineHeight = parseFloat(style.lineHeight || '');
  const fontCandidates = (el.dataset.fontFamily || style.fontFamily || 'Noto Sans SC').split(',').map((item) => item.trim().replace(/^['\"]|['\"]$/g, '')).filter(Boolean);
  const resolvedFontFamily = fontCandidates.find((font) => document.fonts.check(`${fontSize}px "${font}"`, sourceText || 'Aa中')) || fontCandidates[0] || 'Arial';
  return {
    text: sourceText,
    x: hasDeclaredBox ? declaredBox[0] : rect.x,
    y: hasDeclaredBox ? declaredBox[1] : rect.y,
    width: hasDeclaredBox ? declaredBox[2] : rect.width,
    height: hasDeclaredBox ? declaredBox[3] : rect.height,
    fontSize,
    fontWeight: el.dataset.bold === 'true' ? '700' : (style.fontWeight || '400'),
    color: style.color || 'rgb(20, 33, 61)',
    lineHeight: Number.isFinite(lineHeight) ? lineHeight : fontSize * 1.45,
    textAlign: el.dataset.align || style.textAlign || 'left',
    fontFamily: resolvedFontFamily,
    fontAvailable: document.fonts.check(`${fontSize}px "${resolvedFontFamily}"`, sourceText || 'Aa中'),
    isListItem: el.tagName === 'LI'
  };
})
"""

HIDE_TEXT_JS = """
() => {
  document.querySelectorAll('[data-text]').forEach((el) => {
    el.style.visibility = 'hidden';
  });
}
"""

TEXTBOX_WIDTH_SLACK = 1.02
TEXTBOX_HEIGHT_SLACK_PX = 1
MIN_TEXTBOX_SIZE_PX = 1
