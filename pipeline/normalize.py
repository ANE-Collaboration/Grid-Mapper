"""
Japanese Transmission Line Name & Voltage Normalization Module.

This module provides deterministic canonicalization routines for Japanese extra-high-voltage
power transmission line names and voltage ratings. It enables entity resolution between
spatial geometries (extracted from OpenStreetMap) and tabular capacity disclosures (published
by Japanese TSOs such as TEPCO, Kansai T&D, Chubu T&D, and OCCTO).

Author: Japan Grid Mapper ETL Pipeline Team
Integrity Mode: Development / Zero-Facade
"""

import re
import unicodedata
from typing import Any

__all__ = ["normalize_line_name", "clean_voltage"]

# =============================================================================
# Pre-compiled Regular Expression Constants for High-Throughput Normalization
# =============================================================================

# 1. Bracket & metadata patterns: ASCII brackets and Japanese full-width brackets
#    NFKC decomposes （...） and ［...］, but does NOT decompose 【...】, 〈...〉, 《...》, 〔...〕.
RE_BRACKETS = re.compile(r"\[.*?\]|\(.*?\)|【.*?】|（.*?）|〈.*?〉|《.*?》|〔.*?〕")

# 2. Voltage patterns within line names (prefix, suffix, and inline kV)
RE_VOLTAGE_INLINE = re.compile(r"(?:(?<=\D)|^)\d+\s*(?:kV|キロボルト)(?=\D|$)", re.IGNORECASE)
RE_VOLTAGE_PREFIX = re.compile(r"^\s*\d+\s*(?:kV|V|キロボルト)\s*", re.IGNORECASE)
RE_VOLTAGE_SUFFIX = re.compile(r"\s*\d+\s*(?:kV|V|キロボルト)\s*$", re.IGNORECASE)

# 3. Circuit and line number variations:
#    3a. "No." circuit indicators (single and compound, e.g. "No.1", "No.1,2", "No.1・2", "No. 1-2")
RE_CIRCUITS_NO = re.compile(r"No\.?\s*[0-9]+(?:\s*[・/,\-]\s*[0-9]+)*(?:号)?(?:線)?", re.IGNORECASE)

#    3b. Circuit count ("回線", e.g. "1回線", "2回線", "1・2回線", "1/2回線")
RE_CIRCUITS_KAISEN = re.compile(r"(?:第\s*)?[0-9]+(?:回線)?(?:\s*[・/,\-]\s*(?:第\s*)?[0-9]+(?:回線)?)*\s*回線")

#    3c. Multi-circuits (e.g., "1・2号", "1/2号", "1-2号", "1,2号", "1号・2号", "1号線・2号線")
RE_CIRCUITS_MULTI = re.compile(r"(?:第\s*)?[0-9]+(?:号)?(?:線)?(?:\s*[・/,\-]\s*(?:第\s*)?[0-9]+(?:号)?(?:線)?)+")

#    3d. Single circuit numbers with optional "第" (e.g., "1号", "第1号", "第１号", "2号線")
RE_CIRCUITS_SINGLE = re.compile(r"(?:第\s*)?[0-9]+\s*号(?:線)?")

#    3e. Line "L" designations (e.g., "富津火力線 1L", "富津火力線1L", "1L・2L", "1・2L")
RE_CIRCUITS_LINE = re.compile(r"(?:(?<=[線\s])|^)[0-9]+(?:L)?(?:\s*[・/,\-]\s*[0-9]+(?:L)?)*L(?:線)?(?:\s|$)", re.IGNORECASE)

#    3f. Japanese celestial stem circuits (e.g., "新栃木線 甲", "新栃木線甲", "乙", "丙")
#    NOTE: Requires preceding '線' or whitespace (optionally separated by delimiters) to preserve proper nouns like '六甲', '甲西', '甲府'.
RE_CIRCUITS_STEM = re.compile(r"(?<=[線\s])[\s\-_　・/,\,、]*[甲乙丙](?:号)?(?:線)?(?=[\s\-_　・/,\,、]|$)")

# 4. Whitespace and delimiter cleanup (spaces, hyphens, underscores, middle dots, slashes, commas)
RE_DELIMITERS = re.compile(r"[\s\-_　・/,\,、]")

# 5. Voltage parsing helpers & unit matchers
RE_DECIMAL_VOLTAGE = re.compile(r"^(\d+)\.0*(?:\s*k?V)?$", re.IGNORECASE)
RE_COMMAS = re.compile(r"(\d+),(\d+)")
RE_SPACE_THOUSANDS = re.compile(r"(\d+)\s+(000)(?!\d)")
RE_DIGITS = re.compile(r"\d+")

# Non-voltage prefix & metadata annotations:
# Frequency (50Hz, 60Hz), Phase (3相, 3φ), Circuit counts (1回線, 2cct),
# Unit/Circuit identifiers (No.1, 第1号, 1号), Wire configurations (3線式, 3W)
RE_VOLTAGE_ANNOTATIONS = re.compile(
    r"(?:No\.?\s*\d+|第\s*\d+\s*(?:号)?|\d+\s*号(?:線)?|\d+(?:[・/,\-]\d+)*\s*(?:Hz|ヘルツ|相|φ|相式|回線|cct|ccts|ckt|線式|[Ww]))",
    re.IGNORECASE,
)

# Explicit unit matchers prioritizing numbers directly attached to kV or V
RE_VOLTAGE_KV = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:kV(?![a-zA-Z])|キロボルト|kilovolts?\b)",
    re.IGNORECASE,
)
RE_VOLTAGE_V = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:V(?![a-zA-Z])|ボルト|volts?\b)",
    re.IGNORECASE,
)



def normalize_line_name(name: Any) -> str:
    """
    Standardize Japanese transmission line names for deterministic entity resolution.

    Applies the following transformation pipeline:
      1. Type check: Non-string, null, or empty inputs return "".
      2. Unicode NFKC normalization: Converts full-width alphanumerics, kana, and spaces.
      3. Bracket stripping: Removes half-width and Japanese bracketed annotations.
      4. Voltage stripping: Removes inline kV ratings, prefixes, and suffixes.
      5. Circuit and line number stripping: Removes 'No.' designations (including compound No.1,2),
         circuit counts ('回線'), multi-circuit markers (1・2号, 1号・2号), single circuits (第1号),
         'L' markings (1L, 1L・2L), and celestial stems (甲/乙).
      6. Corporate & terminal suffix stripping: Removes '送電線', '線路', and '線'.
         Preserves functional facility names like '開閉所連絡' (Minami-Iwaki tie line).
      7. Post-suffix cleanup: Strips trailing unmasked voltage ratings and orphaned prefixes.
      8. Delimiter & whitespace stripping: Strips all internal delimiters (spaces, middle dots,
         slashes, commas, hyphens) and trims punctuation.

    Parameters:
        name (Any): Raw transmission line name string.

    Returns:
        str: Canonical normalized line name token.

    Examples:
        >>> normalize_line_name("新秦野線 １・２号 [500kV]")
        '新秦野'
        >>> normalize_line_name("南いわき開閉所連絡線 No.1")
        '南いわき開閉所連絡'
        >>> normalize_line_name("東京中線（２７５ｋＶ）")
        '東京中'
        >>> normalize_line_name("新秦野線 1号・2号")
        '新秦野'
        >>> normalize_line_name("新栃木線甲")
        '新栃木'
        >>> normalize_line_name("六甲線")
        '六甲'
    """
    if name is None or not isinstance(name, str):
        return ""
    s = name.strip()
    if not s:
        return ""

    # Step 1: NFKC Unicode Normalization
    s = unicodedata.normalize("NFKC", s)

    # Step 2: Bracket & Metadata Removal
    s = RE_BRACKETS.sub("", s)

    # Step 3: Voltage Removal (Inline, Prefix, Suffix)
    s = RE_VOLTAGE_INLINE.sub(" ", s)
    s = RE_VOLTAGE_PREFIX.sub("", s)
    s = RE_VOLTAGE_SUFFIX.sub("", s)

    # Step 4: Circuit & Line Number Removal
    # Strict precedence: No., 回線, and Line 'L' before multi-circuit; multi before single circuit
    s = RE_CIRCUITS_NO.sub(" ", s)
    s = RE_CIRCUITS_KAISEN.sub(" ", s)
    s = RE_CIRCUITS_LINE.sub(" ", s)
    s = RE_CIRCUITS_MULTI.sub(" ", s)
    s = RE_CIRCUITS_SINGLE.sub(" ", s)
    s = RE_CIRCUITS_STEM.sub(" ", s)

    # Step 5: Corporate and Terminal Suffix Stripping
    # Suffixes '送電線路', '送電線', '線路', and terminal '線' are removed.
    # Note: '開閉所連絡' is preserved as the functional connection name.
    s = s.replace("送電線路", "").replace("送電線", "").replace("線路", "").replace("線", "")

    # Step 6: Post-suffix cleanup for unmasked voltage or orphaned circuit prefixes
    s = RE_VOLTAGE_SUFFIX.sub("", s)
    s = re.sub(r"(?:(?<![a-zA-Z])No\.?|第)$", "", s.strip(), flags=re.IGNORECASE)

    # Step 7: Delimiter and Whitespace Stripping
    s = RE_DELIMITERS.sub("", s)

    return s.strip(" .・/,-_、")


def clean_voltage(voltage_str: Any) -> int:
    """
    Extract nominal voltage in kilovolts (kV) as an integer.

    Handles diverse formats from OpenStreetMap, TSO disclosures, and numeric types:
      - Annotated strings: "50Hz 500kV" -> 500, "3相 66kV" -> 66, "1回線 500kV" -> 500
      - Raw Volt strings: "500000" -> 500, "66000" -> 66
      - Suffix notation: "500kV" -> 500, "154 kV" -> 154
      - Full-width strings: "２７５ｋＶ" -> 275, "６６，０００Ｖ" -> 66
      - Multi-voltage tags: "500000;275000" -> 500, "275000/154000" -> 275
      - Numeric representations: 500000 (int) -> 500, 66.0 (float) -> 66
      - Missing or unparseable values: None, "", "N/A", "-" -> 0

    Parameters:
        voltage_str (Any): Raw voltage representation.

    Returns:
        int: Nominal voltage in kV (e.g. 500, 275, 187, 154, 77, 66), or 0 if unparseable.

    Examples:
        >>> clean_voltage("50Hz 500kV")
        500
        >>> clean_voltage("60Hz 275kV")
        275
        >>> clean_voltage("3相 66kV")
        66
        >>> clean_voltage("1回線 500kV")
        500
    """
    if voltage_str is None or isinstance(voltage_str, (list, dict, set, tuple, bool)):
        return 0

    s = str(voltage_str).strip()
    if not s or s.lower() in ("nan", "none", "n/a", "-", "null", "不明", "未定", "inf", "-inf"):
        return 0

    # Step 1: NFKC Normalization (full-width to half-width, kana normalization)
    norm = unicodedata.normalize("NFKC", s)

    # Step 2: Multi-voltage corridors (take first corridor: semicolons or slashes)
    for sep in (";", "/"):
        if sep in norm:
            norm = norm.split(sep)[0].strip()

    # Step 3: Handle decimal floats formatted as strings (e.g. "66.0" or "66.0 kV")
    m_dec = RE_DECIMAL_VOLTAGE.match(norm)
    if m_dec:
        norm = m_dec.group(1)

    # Step 4: Prioritize explicit kV unit (fast path directly resolving ratings)
    m_kv = RE_VOLTAGE_KV.search(norm)
    if m_kv:
        try:
            return int(round(float(m_kv.group(1))))
        except ValueError:
            pass

    # Step 5: Strip non-voltage annotations (frequency, phase, circuits, numbers)
    norm = RE_VOLTAGE_ANNOTATIONS.sub("", norm)

    # Step 6: Remove comma and space thousands separators (e.g. "500,000" or "500 000 V")
    norm = RE_COMMAS.sub(r"\1\2", norm)
    norm = RE_SPACE_THOUSANDS.sub(r"\1\2", norm)

    # Step 7: Prioritize explicit V unit
    m_v = RE_VOLTAGE_V.search(norm)
    if m_v:
        try:
            val = int(round(float(m_v.group(1))))
            if val >= 1000:
                val = val // 1000
            return val
        except ValueError:
            pass

    # Step 8: Fallback to first remaining digit sequence
    digits = RE_DIGITS.findall(norm)
    if not digits:
        return 0
    val = int(digits[0])

    # Step 9: Scale Volts to Kilovolts
    has_kv = "kv" in norm.lower() or "キロボルト" in norm
    if val >= 1000 and not has_kv:
        val = val // 1000

    return val
