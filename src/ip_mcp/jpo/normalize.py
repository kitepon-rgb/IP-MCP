"""Patent number normalization for the JPO 特許情報取得API.

The JPO API requires application/publication/registration numbers as
half-width 10 digits. Era-year notation (令和N年特願…) is rejected.
"""

from __future__ import annotations

import re
import unicodedata

WAREKI_ERAS: dict[str, int] = {
    "令和": 2018,
    "平成": 1988,
    "昭和": 1925,
    "大正": 1911,
    "明治": 1867,
}


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", value or ""))


def normalize_publication_number(value: str) -> str:
    """西暦4桁 + 連番6桁 = 10桁の公開番号を返す。"""
    digits = _digits_only(value)
    if len(digits) != 10:
        raise ValueError(
            f"公開番号は西暦4桁+6桁の10桁で指定してください (got {len(digits)} digits)"
        )
    return digits


def normalize_application_number(value: str) -> str:
    """西暦4桁 + 連番6桁 = 10桁の出願番号を返す。"""
    digits = _digits_only(value)
    if len(digits) != 10:
        raise ValueError(
            f"出願番号は西暦4桁+6桁の10桁で指定してください (got {len(digits)} digits)"
        )
    return digits


def normalize_registration_number(value: str) -> str:
    """登録番号 (4桁以上の数字) を返す。"""
    digits = _digits_only(value)
    if len(digits) < 4:
        raise ValueError(f"登録番号は4桁以上の数字で指定してください (got {len(digits)})")
    return digits


def convert_wareki_year(era: str, year: str | int) -> int:
    """令和N年 → 西暦に変換。"""
    if era not in WAREKI_ERAS:
        raise ValueError(f"未対応の元号: {era}")
    return WAREKI_ERAS[era] + int(year)


_PUBLICATION_PATTERNS = [
    re.compile(r"(特開|特表|特公)\s*(\d{4})[-ー－]?\s*(\d{4,6})"),
    re.compile(r"(特開|特表|特公)\s*(令和|平成|昭和)\s*(\d{1,2})[-ー－]?\s*(\d{4,6})"),
    re.compile(r"^JP[-_]?(\d{4})[-_]?(\d{4,6})$", re.IGNORECASE),
]
_APPLICATION_PATTERNS = [
    re.compile(r"特願\s*(\d{4})[-ー－]?\s*(\d{4,6})"),
    re.compile(r"特願\s*(令和|平成|昭和)\s*(\d{1,2})[-ー－]?\s*(\d{4,6})"),
]


def parse_identifier(value: str) -> tuple[str, str]:
    """ユーザー入力から (種別, 10桁番号) を返す。

    種別は "application" / "publication" / "registration" のいずれか。
    マッチしない場合は ValueError。
    """
    text = unicodedata.normalize("NFKC", (value or "").strip())
    if not text:
        raise ValueError("identifier is empty")

    # Application number: 特願YYYY-NNNNNN or 特願元号N年-NNNNNN
    for pattern in _APPLICATION_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if m.group(1) in WAREKI_ERAS:
            year = convert_wareki_year(m.group(1), m.group(2))
            serial = m.group(3).zfill(6)
        else:
            year = int(m.group(1))
            serial = m.group(2).zfill(6)
        return "application", f"{year}{serial}"

    # Publication number: 特開YYYY-NNNNNN or JP-YYYY-NNNNNN
    for pattern in _PUBLICATION_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if pattern.pattern.startswith("^JP"):
            year = int(m.group(1))
            serial = m.group(2).zfill(6)
        elif m.group(2) in WAREKI_ERAS:
            year = convert_wareki_year(m.group(2), m.group(3))
            serial = m.group(4).zfill(6)
        else:
            year = int(m.group(2))
            serial = m.group(3).zfill(6)
        return "publication", f"{year}{serial}"

    # Bare 10-digit number: assume application
    digits = _digits_only(text)
    if len(digits) == 10:
        return "application", digits

    # 4-7 digit number: assume registration
    if 4 <= len(digits) <= 7:
        return "registration", digits

    raise ValueError(f"番号の形式を判別できませんでした: {value!r}")
