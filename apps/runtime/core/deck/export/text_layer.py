"""Text-layer JavaScript and geometry constants shared by render/export code."""

TEXT_LAYER_JS = """
() => Array.from(document.querySelectorAll('[data-text]')).map((el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const fontSize = parseFloat(style.fontSize || '18');
  const lineHeight = parseFloat(style.lineHeight || '');
  const fontCandidates = (style.fontFamily || 'Noto Sans SC').split(',').map((item) => item.trim().replace(/^['\"]|['\"]$/g, '')).filter(Boolean);
  const resolvedFontFamily = fontCandidates.find((font) => document.fonts.check(`${fontSize}px "${font}"`, el.innerText || 'Aa中')) || fontCandidates[0] || 'Arial';
  return {
    text: el.innerText || el.textContent || '',
    x: rect.x,
    y: rect.y,
    width: rect.width,
    height: rect.height,
    fontSize,
    fontWeight: style.fontWeight || '400',
    color: style.color || 'rgb(20, 33, 61)',
    lineHeight: Number.isFinite(lineHeight) ? lineHeight : fontSize * 1.45,
    textAlign: style.textAlign || 'left',
    fontFamily: resolvedFontFamily,
    fontAvailable: document.fonts.check(`${fontSize}px "${resolvedFontFamily}"`, el.innerText || 'Aa中'),
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
