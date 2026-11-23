const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff]/;
const KANA_RE = /[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]/;
const HANGUL_RE = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/;
const LATIN_RE = /[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff]/;
const LETTER_RE = /\p{L}/u;
const DIGIT_RE = /[0-9]/;

const LATIN_TARGET_LANGS = new Set(["en", "fr", "es", "it", "de", "pt"]);

function isAcronymOrTechnicalToken(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (DIGIT_RE.test(trimmed)) return true;
  const asciiLetters = trimmed.replace(/[^A-Za-z]/g, "");
  return asciiLetters.length >= 2 && asciiLetters === asciiLetters.toUpperCase() && !CJK_RE.test(trimmed) && asciiLetters.length <= 16;
}

export function sanitizeTargetForLang(
  rawTrans: string,
  rawSource: string,
  targetLang: string,
): string {
  if (!rawTrans) return "";
  const lang = (targetLang || "").trim().toLowerCase().split("-")[0];

  if (lang === "zh") {
    if (CJK_RE.test(rawTrans) || !LETTER_RE.test(rawTrans) || CJK_RE.test(rawSource)) {
      return rawTrans;
    }
    if (isAcronymOrTechnicalToken(rawTrans)) {
      return rawTrans;
    }
    return "";
  }

  if (LATIN_TARGET_LANGS.has(lang)) {
    if (LATIN_RE.test(rawTrans)) {
      return rawTrans;
    }
    if (CJK_RE.test(rawTrans)) {
      return "";
    }
    return rawTrans;
  }

  if (lang === "ja") {
    if (KANA_RE.test(rawTrans) || (CJK_RE.test(rawTrans) && !HANGUL_RE.test(rawTrans))) {
      return rawTrans;
    }
    if (!LETTER_RE.test(rawTrans) || isAcronymOrTechnicalToken(rawTrans)) {
      return rawTrans;
    }
    return "";
  }

  if (lang === "ko") {
    if (HANGUL_RE.test(rawTrans) || !LETTER_RE.test(rawTrans) || isAcronymOrTechnicalToken(rawTrans)) {
      return rawTrans;
    }
    return "";
  }

  return rawTrans;
}
