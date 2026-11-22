from __future__ import annotations

import io
from typing import Final

from PIL import Image

from src.utils.console import log

# NEXUSPHP Captcha uses a custom 8x10 bitmap font with 2px strokes
_TEMPLATE_W: Final[int] = 8
_TEMPLATE_H: Final[int] = 10
_TEMPLATE_SIZE: Final[tuple[int, int]] = (_TEMPLATE_W, _TEMPLATE_H)
_CHARSET: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_MIN_CONFIDENCE: Final[float] = 0.45

_CHAR_X_POSITIONS: Final[list[int]] = [24, 42, 60, 78, 96, 114]
_CHAR_Y_START: Final[int] = 14
_CHAR_Y_END: Final[int] = 24

_FONT_PATTERNS: Final[dict[str, list[int]]] = {
    # NOTE only encoutered these 21 characters through 100s of tests across 5 sites
    # even if it fails should work on retry  (Missing: 0, I, J, K, L, O, Q, S, T, U, V, W, X, Y, Z)
    "1": [0,0,0,1,1,0,0,0, 0,0,1,1,1,0,0,0, 0,1,1,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,0,0,1,1,0,0,0, 0,1,1,1,1,1,1,0],
    "2": [0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,1, 0,0,0,0,0,0,1,1, 0,0,0,0,0,1,1,0, 0,0,0,0,1,1,0,0, 0,0,0,1,1,0,0,0, 0,0,1,1,0,0,0,0, 0,1,1,0,0,0,0,0, 1,1,1,1,1,1,1,1],
    "3": [0,1,1,1,1,1,0,0, 1,1,0,0,0,1,1,0, 0,0,0,0,0,0,1,1, 0,0,0,0,0,1,1,0, 0,0,0,1,1,1,0,0, 0,0,0,0,0,1,1,0, 0,0,0,0,0,0,1,1, 0,0,0,0,0,0,1,1, 1,1,0,0,0,1,1,0, 0,1,1,1,1,1,0,0],
    "4": [0,0,0,0,0,1,1,0, 0,0,0,0,1,1,1,0, 0,0,0,1,1,1,1,0, 0,0,1,1,0,1,1,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,1,1,0, 1,1,1,1,1,1,1,1, 0,0,0,0,0,1,1,0, 0,0,0,0,0,1,1,0, 0,0,0,0,0,1,1,0],
    "5": [1,1,1,1,1,1,1,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,1,1,1,0,0, 1,1,1,0,0,1,1,0, 0,0,0,0,0,0,1,1, 0,0,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 0,1,1,0,0,1,1,0, 0,0,1,1,1,1,0,0],
    "6": [0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,0, 1,1,0,0,0,0,0,0, 1,1,0,1,1,1,0,0, 1,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 0,1,1,0,0,1,1,0, 0,0,1,1,1,1,0,0],
    "7": [1,1,1,1,1,1,1,1, 0,0,0,0,0,0,1,1, 0,0,0,0,0,0,1,1, 0,0,0,0,0,1,1,0, 0,0,0,0,1,1,0,0, 0,0,0,1,1,0,0,0, 0,0,1,1,0,0,0,0, 0,1,1,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0],
    "8": [0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,1,0,0,1,1, 0,1,1,0,0,1,1,0, 0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 0,1,1,0,0,1,1,0, 0,0,1,1,1,1,0,0],
    "9": [0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 0,1,1,0,0,1,1,1, 0,0,1,1,1,0,1,1, 0,0,0,0,0,0,1,1, 0,1,0,0,0,0,1,1, 0,1,1,0,0,1,1,0, 0,0,1,1,1,1,0,0],
    "A": [0,0,0,1,1,0,0,0, 0,0,1,1,1,1,0,0, 0,1,1,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,1,1,1,1,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1],
    "B": [1,1,1,1,1,1,0,0, 1,1,0,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,1,1,0, 1,1,1,1,1,1,0,0, 1,1,0,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,1,1,0, 1,1,1,1,1,1,0,0],
    "C": [0,0,1,1,1,1,1,0, 0,1,1,0,0,0,1,1, 1,1,0,0,0,0,0,1, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,1, 0,1,1,0,0,0,1,1, 0,0,1,1,1,1,1,0],
    "D": [1,1,1,1,1,1,0,0, 1,1,0,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,1,1,0, 1,1,1,1,1,1,0,0],
    "E": [1,1,1,1,1,1,1,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,1,1,1,1,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,1,1,1,1,1,0],
    "F": [1,1,1,1,1,1,1,1, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,1,1,1,1,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0],
    "G": [0,0,1,1,1,1,1,0, 0,1,1,0,0,0,1,1, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,1,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 0,1,1,0,0,0,1,1, 0,0,1,1,1,1,1,0],
    "H": [1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,1,1,1,1,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1],
    "M": [1,1,0,0,0,0,1,1, 1,1,1,0,0,1,1,1, 1,1,1,1,1,1,1,1, 1,1,0,1,1,0,1,1, 1,1,0,1,1,0,1,1, 1,1,0,1,1,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1],
    "N": [1,1,0,0,0,0,1,1, 1,1,1,0,0,0,1,1, 1,1,1,1,0,0,1,1, 1,1,1,1,0,0,1,1, 1,1,0,1,1,0,1,1, 1,1,0,1,1,0,1,1, 1,1,0,0,1,1,1,1, 1,1,0,0,0,1,1,1, 1,1,0,0,0,1,1,1, 1,1,0,0,0,0,1,1],
    "P": [1,1,1,1,1,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,1,1,1,1,1,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0, 1,1,0,0,0,0,0,0],
    "R": [1,1,1,1,1,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1, 1,1,1,1,1,1,1,0, 1,1,1,1,1,0,0,0, 1,1,0,0,1,1,0,0, 1,1,0,0,0,1,1,0, 1,1,0,0,0,0,1,1, 1,1,0,0,0,0,1,1],
}  # fmt:off


_BIT_COUNT: Final[list[int]] = [bin(i).count("1") for i in range(256)]


def _get_templates() -> dict[str, tuple[int, ...]]:
    """Convert font patterns into tuples of integers for fast bitwise matching."""
    templates = {}
    for char, data in _FONT_PATTERNS.items():
        rows = []
        for r in range(_TEMPLATE_H):
            row_val = 0
            for c in range(_TEMPLATE_W):
                if data[r * _TEMPLATE_W + c]:
                    row_val |= 1 << c
            rows.append(row_val)
        templates[char] = tuple(rows)
    return templates


_templates: Final[dict[str, tuple[int, ...]]] = _get_templates()
_BINARIZE_LUT: Final[list[int]] = [0] * 128 + [255] * 128


def _match_character(char_img: Image.Image) -> tuple[str, float]:
    """Match an 8x10 segment against templates using Bitwise Hamming distance."""
    if char_img.size != _TEMPLATE_SIZE:
        char_img = char_img.resize(_TEMPLATE_SIZE, Image.Resampling.NEAREST)

    if char_img.mode != "1":
        char_img = char_img.convert("1")

    # extract pixels into a tuple of 10 row integers
    #  map 0 (black) to 1 (bit set)
    data = list(char_img.getdata())
    char_rows = []
    for r in range(_TEMPLATE_H):
        row_val = 0
        for c in range(_TEMPLATE_W):
            if data[r * _TEMPLATE_W + c] == 0:
                row_val |= 1 << c
        char_rows.append(row_val)

    best_char = "?"
    best_score = -1.0

    for char, template_rows in _templates.items():
        # dx = 0
        diff_bits = 0
        for r1, r2 in zip(char_rows, template_rows):
            diff_bits += _BIT_COUNT[(r1 ^ r2) & 0xFF]
        score = (80 - diff_bits) / 80.0
        if score > best_score:
            best_score = score
            best_char = char

        # dx = -1
        diff_bits = 0
        for r1, r2 in zip(char_rows, template_rows):
            diff_bits += _BIT_COUNT[((r1 >> 1) ^ r2) & 0xFF]
        score = (80 - diff_bits) / 80.0
        if score > best_score:
            best_score = score
            best_char = char

        # dx = 1
        diff_bits = 0
        for r1, r2 in zip(char_rows, template_rows):
            diff_bits += _BIT_COUNT[(((r1 << 1) & 0xFF) ^ r2) & 0xFF]
        score = (80 - diff_bits) / 80.0
        if score > best_score:
            best_score = score
            best_char = char

    return best_char, best_score


def solve_nexusphp_captcha(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    gray = img.convert("L")
    w, h = gray.size

    # remove border
    border = 1
    if w > border * 2 and h > border * 2:
        gray = gray.crop((border, border, w - border, h - border))
        w, h = gray.size

    # binarization using LUT
    bw = gray.point(_BINARIZE_LUT, mode="1")

    result: list[str] = []
    for idx, x in enumerate(_CHAR_X_POSITIONS):
        seg = bw.crop((x, _CHAR_Y_START, x + _TEMPLATE_W, _CHAR_Y_END))

        char, score = _match_character(seg)
        log.debug(f"CAPTCHA char {idx}: fixed-position at x={x} -> matched '{char}' score={score:.3f}")

        if score < _MIN_CONFIDENCE:
            raise RuntimeError(f"Low confidence ({score:.2f}) for char {idx} matched as '{char}'")
        result.append(char)

    solved = "".join(result)
    log.debug(f"CAPTCHA solved: {solved!r}")
    return solved
