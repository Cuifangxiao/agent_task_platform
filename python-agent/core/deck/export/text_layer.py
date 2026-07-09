"""Text-layer JavaScript and geometry constants shared by render/export code."""

TEXT_LAYER_JS = """
() => Array.from(document.querySelectorAll('[data-text]')).map((el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  const fontSize = parseFloat(style.fontSize || '18');
  const lineHeight = parseFloat(style.lineHeight || '');
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
    fontFamily: style.fontFamily || 'Noto Sans SC'
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
