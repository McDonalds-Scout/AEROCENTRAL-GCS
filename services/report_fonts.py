from __future__ import annotations

from pathlib import Path

CHINESE_FONT_CANDIDATES = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Arial Unicode MS",
]

HTML_FONT_STACK = (
    '"Noto Sans CJK SC", "Microsoft YaHei", "SimHei", '
    '"Arial Unicode MS", Arial, sans-serif'
)


def configure_plot_fonts() -> str | None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None

    available = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in CHINESE_FONT_CANDIDATES if name in available), None)
    font_list = ([selected] if selected else []) + ["DejaVu Sans", "Arial", "sans-serif"]
    plt.rcParams["font.sans-serif"] = font_list
    plt.rcParams["axes.unicode_minus"] = False
    return selected


def set_run_font(run, east_asia="Microsoft YaHei", latin="Calibri"):
    run.font.name = latin
    try:
        run._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", east_asia)
        run._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ascii", latin)
        run._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hAnsi", latin)
    except Exception:
        pass


def set_paragraph_font(paragraph, east_asia="Microsoft YaHei", latin="Calibri"):
    for run in paragraph.runs:
        set_run_font(run, east_asia=east_asia, latin=latin)


def set_docx_fonts(document, east_asia="Microsoft YaHei", latin="Calibri"):
    for style in document.styles:
        try:
            style.font.name = latin
            style._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", east_asia)
            style._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ascii", latin)
            style._element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hAnsi", latin)
        except Exception:
            continue
    for paragraph in document.paragraphs:
        set_paragraph_font(paragraph, east_asia=east_asia, latin=latin)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    set_paragraph_font(paragraph, east_asia=east_asia, latin=latin)
    return document


def html_font_css() -> str:
    return f"""
body,table,th,td,p,li,h1,h2,h3,h4,pre{{font-family:{HTML_FONT_STACK};}}
body{{max-width:980px;margin:32px auto;line-height:1.65;color:#172027}}
h1{{color:#0b2545;font-size:28px}}h2{{color:#2E74B5;border-bottom:1px solid #d8e1ea;padding-bottom:4px}}h3{{color:#1F4D78}}
img{{max-width:100%;border:1px solid #d7dee6;margin:8px 0 18px}}pre{{background:#f4f6f9;padding:8px;white-space:pre-wrap}}
table{{border-collapse:collapse;width:100%;margin:10px 0 18px}}th,td{{border:1px solid #d7dee6;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eef3f7}}
"""


def likely_has_chinese_pdf_font() -> bool:
    windows_fonts = Path("C:/Windows/Fonts")
    if windows_fonts.exists():
        for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
            if (windows_fonts / name).exists():
                return True
    return True
