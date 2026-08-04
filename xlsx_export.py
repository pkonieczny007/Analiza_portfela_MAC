"""Minimalny generator .xlsx (Office Open XML) na czystej stdlib (zipfile+XML).

Rozszerzenie generatora z BOT_AGG1/app/services/xlsx_export.py o WIELE
arkuszy: build_xlsx([(nazwa, naglowki, wiersze), ...]) -> bytes.
Liczby zapisywane jako liczby (Excel moze sumowac), teksty jako inline strings.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def _col_letter(idx: int) -> str:
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell_xml(row: int, col: int, value) -> str:
    ref = f"{_col_letter(col)}{row}"
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "TAK" if value else "NIE"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" t="n"><v>{value!r}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(headers: list, rows: list[list]) -> str:
    all_rows = [list(headers)] + [list(r) for r in rows]
    row_xml = []
    for r_i, row in enumerate(all_rows, start=1):
        cells = "".join(_cell_xml(r_i, c_i, v) for c_i, v in enumerate(row, start=1))
        row_xml.append(f'<row r="{r_i}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )


def build_xlsx(sheets: list[tuple[str, list, list[list]]]) -> bytes:
    """sheets: lista (nazwa_arkusza, naglowki, wiersze)."""
    if not sheets:
        raise ValueError("Brak arkuszy")

    sheet_tags, rel_tags, overrides = [], [], []
    for i, (name, _h, _r) in enumerate(sheets, start=1):
        safe = escape((name or f"Arkusz{i}")[:31])
        sheet_tags.append(f'<sheet name="{safe}" sheetId="{i}" r:id="rId{i}"/>')
        rel_tags.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_tags)}</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(rel_tags)}</Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'{"".join(overrides)}</Types>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for i, (_name, headers, rows) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(headers, rows))
    return buf.getvalue()
