#!/usr/bin/env python3
"""
Markdown to PDF converter with Chinese font support and theme system.

Converts markdown files to PDF using:
  - pandoc (markdown → HTML)
  - weasyprint or headless Chrome (HTML → PDF), auto-detected

Usage:
    python md_to_pdf.py input.md output.pdf
    python md_to_pdf.py input.md --theme warm-terra
    python md_to_pdf.py input.md --theme default --backend chrome
    python md_to_pdf.py input.md  # outputs input.pdf, default theme, auto backend

Themes:
    Stored in ../themes/*.css. Built-in themes:
    - default:     Songti SC + black/grey, formal documents
    - warm-terra:  PingFang SC + terra cotta, training/workshop materials

Requirements:
    pandoc (system install, e.g. brew install pandoc)
    weasyprint (pip install weasyprint) OR Google Chrome (for --backend chrome)
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
THEMES_DIR = SCRIPT_DIR.parent / "themes"

# macOS ARM: auto-configure library path for weasyprint
if platform.system() == "Darwin":
    _homebrew_lib = "/opt/homebrew/lib"
    if Path(_homebrew_lib).is_dir():
        _cur = os.environ.get("DYLD_LIBRARY_PATH", "")
        if _homebrew_lib not in _cur:
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{_homebrew_lib}:{_cur}" if _cur else _homebrew_lib
            )


def _find_chrome() -> str | None:
    """Find Chrome/Chromium binary path."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


def _has_weasyprint() -> bool:
    """Check if weasyprint is importable."""
    try:
        import weasyprint  # noqa: F401

        return True
    except ImportError:
        return False


def _detect_backend() -> str:
    """Auto-detect best available backend: weasyprint > chrome."""
    if _has_weasyprint():
        return "weasyprint"
    if _find_chrome():
        return "chrome"
    print(
        "Error: No PDF backend found. Install weasyprint (pip install weasyprint) "
        "or Google Chrome.",
        file=sys.stderr,
    )
    sys.exit(1)


def _load_theme(theme_name: str) -> str:
    """Load CSS from themes directory."""
    theme_file = THEMES_DIR / f"{theme_name}.css"
    if not theme_file.exists():
        available = [f.stem for f in THEMES_DIR.glob("*.css")]
        print(
            f"Error: Theme '{theme_name}' not found. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(1)
    return theme_file.read_text(encoding="utf-8")


def _list_themes() -> list[str]:
    """List available theme names."""
    if not THEMES_DIR.exists():
        return []
    return sorted(f.stem for f in THEMES_DIR.glob("*.css"))


def _ensure_hr_spacing(text: str) -> str:
    """Ensure blank lines before and after standalone --- (thematic break / HR) lines.

    Without blank lines, pandoc treats '---' immediately after a paragraph/table row
    as a setext-style h2 heading marker, which corrupts the document structure.
    This function adds blank lines around any '---' line that is not a table separator
    (table separators contain '|' characters like '|---|---|').
    """
    lines = text.split("\n")
    result = []
    hr_re = re.compile(r"^---+$")  # 純粋な --- のみ（| を含まない）
    for i, line in enumerate(lines):
        if hr_re.match(line):
            # 前の行が空でなければ空行を挿入
            if result and result[-1].strip():
                result.append("")
            result.append(line)
            # 次の行が空でなければ空行を挿入（先読み）
            if i + 1 < len(lines) and lines[i + 1].strip():
                result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def _ensure_list_spacing(text: str) -> str:
    """Ensure blank lines before list items for proper markdown parsing.

    Both Python markdown library and pandoc require a blank line before a list
    when it follows a paragraph. Without it, list items render as plain text.
    """
    lines = text.split("\n")
    result = []
    list_re = re.compile(r"^(\s*)([-*+]|\d+\.)\s")
    for i, line in enumerate(lines):
        if i > 0 and list_re.match(line):
            prev = lines[i - 1]
            if prev.strip() and not list_re.match(prev):
                result.append("")
        result.append(line)
    return "\n".join(result)


def _convert_pdf_to_png(pdf_path: Path) -> Path | None:
    """PDF の1ページ目を PNG に変換して返す。失敗時は None。

    出力ファイルは PDF と同じディレクトリに <stem>_page1.png として保存する。
    既に存在する場合は再変換をスキップする。
    """
    if not shutil.which("pdftoppm"):
        print("Warning: pdftoppm not found. Install poppler (brew install poppler).", file=sys.stderr)
        return None

    png_path = pdf_path.with_name(pdf_path.stem + "_page1.png")
    if png_path.exists():
        return png_path

    out_stem = str(pdf_path.with_name(pdf_path.stem + "_page1"))
    result = subprocess.run(
        ["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf_path), out_stem],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not png_path.exists():
        print(f"Warning: pdftoppm failed for {pdf_path}: {result.stderr}", file=sys.stderr)
        return None
    return png_path


def _preprocess_pdf_references(text: str, base_dir: Path) -> str:
    """Markdown 中の PDF 参照を PNG に自動変換する。

    対象パターン:
    - ![alt](path.pdf)  → ![alt](path_page1.png)  （画像記法）
    - [text](path.pdf)  → ![text](path_page1.png) （リンク記法 → 画像に昇格）

    リモート URL（http/https）はスキップする。
    pdftoppm が未インストールの場合はそのまま残す。
    """
    # 画像記法: ![alt](*.pdf)  ― URL 中の () を許容するため .+? を使う
    img_re = re.compile(r"!\[([^\]]*)\]\((.+?\.pdf)\)", re.IGNORECASE)
    # リンク記法: [text](*.pdf)  ― 先頭が ! でないもの
    link_re = re.compile(r"(?<!!)\[([^\]]*)\]\((.+?\.pdf)\)", re.IGNORECASE)

    def _replace(match: re.Match) -> str:
        alt = match.group(1)
        pdf_ref = match.group(2)

        # リモート URL はスキップ
        if pdf_ref.startswith(("http://", "https://")):
            return match.group(0)

        # 絶対パスでなければ base_dir からの相対パスとして解決
        if not pdf_ref.startswith("/"):
            pdf_path = (base_dir / pdf_ref).resolve()
        else:
            pdf_path = Path(pdf_ref)

        if not pdf_path.exists():
            print(f"Warning: PDF not found: {pdf_path}", file=sys.stderr)
            return match.group(0)

        png_path = _convert_pdf_to_png(pdf_path)
        if png_path is None:
            return match.group(0)

        # base_dir からの相対パスで返す
        try:
            rel = png_path.relative_to(base_dir)
            return f"![{alt}]({rel})"
        except ValueError:
            return f"![{alt}]({png_path})"

    text = img_re.sub(_replace, text)
    text = link_re.sub(_replace, text)
    return text


_CJK_RANGE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002ebef"
    r"\u3000-\u303f\uff00-\uffef]"
)


def _fix_cjk_code_blocks(html: str) -> str:
    """Replace <pre><code> blocks containing CJK with styled divs.

    weasyprint renders <pre> blocks using monospace fonts that lack CJK glyphs,
    causing garbled output. This converts CJK-heavy code blocks to styled divs
    that use the document's CJK font stack instead.
    """

    def _replace_if_cjk(match: re.Match) -> str:
        content = match.group(1)
        if _CJK_RANGE.search(content):
            return f'<div class="cjk-code-block">{content}</div>'
        return match.group(0)

    return re.sub(
        r"<pre><code(?:\s[^>]*)?>(.+?)</code></pre>",
        _replace_if_cjk,
        html,
        flags=re.DOTALL,
    )


def _insert_camelcase_breaks(html: str) -> str:
    """インライン <code> 要素内の camelCase 境界に <wbr> を挿入する。

    `paymentDate` → `payment<wbr>Date` のように変換することで，
    狭いテーブルセル内でも意味のある位置で折り返しが発生する。
    <pre><code> ブロックには適用しない（コードサンプルの改行を壊さないため）。
    """
    # <pre> ブロックを一時的に保護する
    pre_blocks: list[str] = []

    def _protect(m: re.Match) -> str:
        idx = len(pre_blocks)
        pre_blocks.append(m.group(0))
        return f"__PREBLOCK_{idx}__"

    html = re.sub(r"<pre>.*?</pre>", _protect, html, flags=re.DOTALL)

    # インライン <code> の中身に <wbr> を挿入（小文字→大文字の境界）
    def _add_wbr(m: re.Match) -> str:
        tag_open = m.group(1)
        content = m.group(2)
        tag_close = m.group(3)
        new_content = re.sub(r"([a-z])([A-Z])", r"\1<wbr>\2", content)
        return f"{tag_open}{new_content}{tag_close}"

    html = re.sub(r"(<code[^>]*>)(.*?)(</code>)", _add_wbr, html, flags=re.DOTALL)

    # <pre> ブロックを元に戻す
    for idx, block in enumerate(pre_blocks):
        html = html.replace(f"__PREBLOCK_{idx}__", block)

    return html


def _md_to_html(md_file: str) -> str:
    """Convert markdown to HTML using pandoc with list spacing preprocessing."""
    if not shutil.which("pandoc"):
        print(
            "Error: pandoc not found. Install with: brew install pandoc",
            file=sys.stderr,
        )
        sys.exit(1)

    md_path = Path(md_file)
    md_content = md_path.read_text(encoding="utf-8")
    md_content = _preprocess_pdf_references(md_content, md_path.parent)
    md_content = _ensure_hr_spacing(md_content)
    md_content = _ensure_list_spacing(md_content)

    result = subprocess.run(
        ["pandoc", "-f", "markdown-yaml_metadata_block", "-t", "html", "--mathml"],
        input=md_content,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error: pandoc failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    html = result.stdout
    html = _fix_cjk_code_blocks(html)
    html = _insert_camelcase_breaks(html)
    return html


def _build_full_html(html_content: str, css: str, title: str) -> str:
    """Wrap HTML content in a full document with CSS."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>{css}</style>
</head>
<body>
{html_content}
</body>
</html>"""


def _normalize_table_separators(text: str) -> str:
    """パイプテーブルのセパレータ行を等幅ダッシュに正規化する。

    pandoc は LaTeX 出力時の列幅をセパレータ行のダッシュ数比率から計算する。
    不均等なダッシュ数（例: |-----------|------|）だと正解列が 8% 幅しか取れず
    セル内容がオーバーフローする。全列を 9 ダッシュに統一して均等幅にする。
    """
    # パイプテーブルのセパレータ行: | と - と : と空白だけで構成され、必ず - を含む
    sep_re = re.compile(r"^\|(?:[\s:]*-+[\s:]*\|)+\s*$", re.MULTILINE)

    def _replace(m: re.Match) -> str:
        line = m.group(0).strip()
        # 先頭・末尾の | を除いた中身を | で分割
        inner_cells = line[1:-1].split("|") if line.startswith("|") and line.endswith("|") else line.split("|")
        return "|" + "|".join(["---------" for _ in inner_cells]) + "|"

    return sep_re.sub(_replace, text)


def _insert_soft_breaks_for_table_tokens(text: str) -> str:
    """LaTeX 表で長い ASCII / camelCase トークンが列外にはみ出すのを抑える。

    テーブル行（| で始まる行）のセル内容にのみ適用し，markdown 画像/リンク記法を壊さない。
    camelCase・アンダースコア・4桁以上の数字に \\hskip0pt を挿入して折り返し候補を増やす。
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # テーブルセル行（| で始まり，セパレータ行でない）のみ処理
        is_table_row = stripped.startswith("|")
        is_separator = is_table_row and all(c in "|-: " for c in stripped)
        if is_table_row and not is_separator:
            # camelCase: storeNameBranch → store\hskip0pt Name\hskip0pt Branch
            line = re.sub(r"([a-z])([A-Z])", r"\1\\hskip0pt \2", line)
            # アンダースコア区切り
            line = re.sub(r"(_)(?=[a-zA-Z])", r"\1\\hskip0pt ", line)
            # 4桁以上の連続数字に折り返し候補
            line = re.sub(r"(\d{4})(?=\d)", r"\1\\hskip0pt ", line)
        result.append(line)
    return "\n".join(result)


_MATH_SYMBOLS = [
    ("≤", r"$\leq$"),
    ("≥", r"$\geq$"),
    ("≠", r"$\neq$"),
    ("→", r"$\rightarrow$"),
    ("←", r"$\leftarrow$"),
    ("×", r"$\times$"),
    ("÷", r"$\div$"),
    ("≈", r"$\approx$"),
    ("±", r"$\pm$"),
]


def _replace_math_symbols(text: str) -> str:
    """LuaLaTeX テキストフォントに含まれない数学記号を LaTeX math コマンドに置換する。

    backtick コードスパン内はスキップ（verbatim 環境でコマンドがリテラルになるため）。
    """
    segments = re.split(r"(`[^`\n]+`)", text)
    result = []
    for idx, seg in enumerate(segments):
        if idx % 2 == 1:
            result.append(seg)
        else:
            for char, cmd in _MATH_SYMBOLS:
                seg = seg.replace(char, cmd)
            result.append(seg)
    return "".join(result)


def _insert_cjk_linebreaks(text: str) -> str:
    """連続する CJK 文字の間に \\hskip0pt を挿入する。

    luatex-ja なしの LuaLaTeX 環境では CJK 文字列が 1 語として扱われ
    p{} 列を超えてしまう。ゼロ幅スキップを挿入することで任意位置での折り返しを許可する。
    pandoc の raw_tex 拡張で \\hskip0pt はそのまま LaTeX コマンドとして通過する。
    backtick コードスパン内はスキップ（pandoc が texttt/verb に変換し \\hskip0pt がリテラル文字列になるため）。
    """
    def _process_segment(seg: str) -> str:
        result: list[str] = []
        prev_cjk = False
        for ch in seg:
            cp = ord(ch)
            is_cjk = (
                0x3000 <= cp <= 0x9FFF   # CJK Unified Ideographs, Hiragana, Katakana など
                or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
                or 0xFF00 <= cp <= 0xFFEF  # 全角英数・記号
            )
            if is_cjk and prev_cjk:
                result.append(r"\hskip0pt ")
            result.append(ch)
            prev_cjk = is_cjk
        return "".join(result)

    # backtick コードスパンを保護: 奇数インデックスがスパン本体
    segments = re.split(r"(`[^`\n]+`)", text)
    return "".join(
        seg if idx % 2 == 1 else _process_segment(seg)
        for idx, seg in enumerate(segments)
    )


def _tex_add_texttt_breaks(tex: str) -> str:
    r"""longtable 内の \texttt{} に camelCase 折り返し候補を挿入する。

    \texttt{storeNameBranch}
      → \texttt{store}\hskip0pt\texttt{NameBranch}

    これにより p{} 列内で camelCase 識別子が適切な位置で折り返せるようになる。
    """

    def _break_camel(m: re.Match) -> str:
        content = m.group(1)

        def _insert_break(m2: re.Match) -> str:
            # 小文字→大文字の境界に }\hskip0pt\texttt{ を挿入
            return m2.group(1) + r"}\hskip0pt\texttt{" + m2.group(2)

        broken = re.sub(r"([a-z])([A-Z])", _insert_break, content)
        if broken == content:
            return m.group(0)  # 変更なし
        return r"\texttt{" + broken + "}"

    return re.sub(r"\\texttt\{([^{}]+)\}", _break_camel, tex)


def _tex_fix_table_columns(tex: str) -> str:
    r"""longtable の単純 l/c/r 列指定を p{} 幅指定に変換してテキスト折り返しを有効にする。

    pandoc が生成する @{}lll...@{} 形式の列指定はテキスト折り返しを行わないため，
    l 列を >{\raggedright\arraybackslash}p{Xcm}，c/r 列を固定幅 p{} に変換する。
    \real{} や \columnwidth を含む既計算済み指定はスキップする。
    """
    PAGE_WIDTH_CM = 17.0   # A4 行幅（左右余白 2cm ずつ）
    TABCOLSEP_CM = 0.07    # \tabcolsep=2pt（ヘッダーで設定）
    SHORT_COL_CM = 1.40    # c/r 列の固定幅

    def replace_colspec(m: re.Match) -> str:
        prefix, colspec, suffix = m.group(1), m.group(2), m.group(3)
        if "real" in colspec or r"\columnwidth" in colspec:
            return m.group(0)

        inner = colspec[len("@{}"):-len("@{}")]
        if not inner or not all(c in "lcr" for c in inner):
            return m.group(0)

        col_types = list(inner)
        n = len(col_types)
        text_n = sum(1 for c in col_types if c == "l")
        short_n = n - text_n

        if text_n == 0:
            return m.group(0)

        short_total = short_n * SHORT_COL_CM
        sep_total = n * TABCOLSEP_CM * 2
        per_text = max(1.8, (PAGE_WIDTH_CM - short_total - sep_total) / text_n)

        new_cols = []
        for t in col_types:
            if t == "l":
                new_cols.append(r">{\raggedright\arraybackslash}" + f"p{{{per_text:.2f}cm}}")
            elif t == "c":
                new_cols.append(r">{\centering\arraybackslash}" + f"p{{{SHORT_COL_CM:.2f}cm}}")
            else:
                new_cols.append(r">{\raggedleft\arraybackslash}" + f"p{{{SHORT_COL_CM:.2f}cm}}")

        new_spec = "@{}" + "".join(new_cols) + "@{}"
        return prefix + new_spec + suffix

    return re.sub(
        r"(\\begin\{longtable\}\[[^\]]*\]\{)(@\{\}[lcr]+@\{\})(\})",
        replace_colspec,
        tex,
    )


def _add_longtable_vlines(tex: str) -> str:
    """longtable の p{} 列に縦罫線（|）を追加する。

    pandoc が生成する @{} >{\raggedright}p{...} ... @{}} 形式の列定義を
    |>{\raggedright}p{...}|...| 形式に変換して列の境界を明確にする。
    """
    # pandoc が生成する列指定の1列分パターン
    # >{\raggedright\arraybackslash}p{(\linewidth - 12\tabcolsep) * \real{0.1429}}
    col_re = re.compile(
        r">\{\\raggedright\\arraybackslash\}"   # >{\raggedright\arraybackslash}
        r"p\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"    # p{...} ネストあり
    )

    def _process(m: re.Match) -> str:
        spec = m.group(0)
        # @{} を削除
        inner = re.sub(r'@\{[^}]*\}', '', spec).strip()
        cols = col_re.findall(inner)
        if not cols:
            return spec
        return '|' + '|'.join(c.strip() for c in cols) + '|'

    # @{} + p{} 列群 + @{} のブロックを置換（複数行対応）
    return re.sub(
        r'@\{\}\s*(?:>\{\\raggedright\\arraybackslash\}p\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)+@\{\}',
        _process,
        tex,
        flags=re.DOTALL,
    )


def _preprocess_pdf_links_for_latex(text: str) -> str:
    """LaTeX 向け PDF リンク前処理。

    リンク記法 [text](path.pdf) を画像記法 ![text](path.pdf) に変換する。
    LaTeX は \\includegraphics で PDF を直接埋め込めるため PNG 変換は不要。
    リモート URL はスキップする。
    """
    link_re = re.compile(r"(?<!!)\[([^\]]*)\]\((.+?\.pdf)\)", re.IGNORECASE)

    def _to_image(m: re.Match) -> str:
        alt, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://")):
            return m.group(0)
        return f"![{alt}]({path})"

    return link_re.sub(_to_image, text)


def _insert_table_long_token_breaks(md: str) -> str:
    """長い ASCII 連続文字列にゼロ幅スペース（U+200B）を挿入する。

    16進ファイル名（`052ac286...jpg`）のような区切り文字のない長いトークンは
    LaTeX が折り返せないため，8 文字ごとにゼロ幅スペースを挿入して折り返し候補を追加する。
    表セルだけでなく段落中のバッククォートスパン内も対象とする。
    LuaLaTeX は U+200B を改行候補として認識する。
    """
    ZWSP = "\u200b"
    CHUNK = 8          # 何文字ごとに挿入するか
    MIN_LEN = 16       # この長さ未満のトークンはスキップ

    def _break_token(token: str) -> str:
        """スペースを含まない長い ASCII トークンを分割する."""
        if len(token) < MIN_LEN:
            return token
        if any(ord(c) > 0x2E7F for c in token):
            return token
        return ZWSP.join(token[i:i + CHUNK] for i in range(0, len(token), CHUNK))

    def _process_text(text: str) -> str:
        return " ".join(_break_token(t) for t in text.split(" "))

    def _process_backtick_spans(line: str) -> str:
        """バッククォートスパン内のみ _process_text を適用する."""
        segments = re.split(r"(`[^`\n]+`)", line)
        return "".join(
            _process_text(seg) if idx % 2 == 1 else seg
            for idx, seg in enumerate(segments)
        )

    result_lines = []
    for line in md.split("\n"):
        stripped = line.strip()
        is_table_row = "|" in line and not re.match(r"\s*\|[-:\s|]+\|\s*$", line)
        if is_table_row:
            # 表セル: セル全体を処理
            parts = line.split("|")
            new_parts = [parts[0]] + [_process_text(p) for p in parts[1:-1]] + [parts[-1]]
            result_lines.append("|".join(new_parts))
        elif stripped and not stripped.startswith("#") and "`" in line:
            # 段落・箇条書き: バッククォートスパン内のみ処理
            result_lines.append(_process_backtick_spans(line))
        else:
            result_lines.append(line)
    return "\n".join(result_lines)


def _adjust_table_column_widths(md: str) -> str:
    """マークダウン表のセパレータ行ダッシュ数をコンテンツ幅に比例させる。

    pandoc はセパレータ行の --- 数を列幅比率 (\\real{...}) として解釈する。
    等長ダッシュだと全列が均等幅になり，短いコンテンツ列が不要に広くなる。
    各列の最大コンテンツ幅（CJK=2, ASCII=1）に比例したダッシュ数を設定する。
    """

    def _char_width(s: str) -> int:
        return sum(2 if ord(c) > 0x2E7F else 1 for c in s)

    lines = md.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # 次行がセパレータ行（|---|---| 形式）であればテーブルと判断
        if (
            "|" in line
            and i + 1 < len(lines)
            and re.match(r"\s*\|[-:\s|]+\|\s*$", lines[i + 1])
        ):
            header_line = line
            sep_line = lines[i + 1]
            body_lines: list[str] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j]:
                body_lines.append(lines[j])
                j += 1

            # 各列の最大コンテンツ幅を計算
            col_max: list[int] = []
            for row in [header_line] + body_lines:
                cells = [c.strip() for c in row.split("|")[1:-1]]
                for ci, cell in enumerate(cells):
                    w = _char_width(cell)
                    if ci < len(col_max):
                        col_max[ci] = max(col_max[ci], w)
                    else:
                        col_max.append(w)

            # 列幅を分類して調整:
            #   テキスト列（長い内容）は MAX_TEXT でキャップ
            #   データ列（短い内容）は MIN_DATA を保証してスコア/カテゴリ列が潰れないようにする
            MAX_TEXT = 20   # 長文テキスト列の上限
            MIN_DATA = 12   # 短い数値・カテゴリ列の下限（CJK ヘッダーが折り返さない最小幅）
            LONG_THRESHOLD = 15  # テキスト/データ列の判定閾値

            capped: list[int] = []
            for w in col_max:
                if w > LONG_THRESHOLD:
                    capped.append(min(w, MAX_TEXT))
                else:
                    capped.append(max(w, MIN_DATA))

            # セパレータ行を再構築（alignment (:---:) は維持）
            sep_cells = [c.strip() for c in sep_line.split("|")[1:-1]]
            new_sep_cells = []
            for ci, sc in enumerate(sep_cells):
                w = capped[ci] if ci < len(capped) else MIN_DATA
                left = ":" if sc.startswith(":") else ""
                right = ":" if (sc.endswith(":") and len(sc) > 1) else ""
                dashes = "-" * max(1, w - len(left) - len(right))
                new_sep_cells.append(left + dashes + right)

            result.append(header_line)
            result.append("|" + "|".join(new_sep_cells) + "|")
            result.extend(body_lines)
            i = j
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


def _has_lualatex() -> bool:
    """Check if lualatex and pandoc are available."""
    return bool(shutil.which("lualatex") and shutil.which("pandoc"))


def _render_lualatex(md_file: str, pdf_file: str) -> None:
    """pandoc + lualatex で PDF を生成する。

    HTML を経由せず pandoc が直接 LaTeX → PDF を生成するため，
    テーブル罫線・日本語・数式・PDF 画像埋め込みが完全に動作する。
    """
    if not _has_lualatex():
        print("Error: lualatex または pandoc が見つかりません。", file=sys.stderr)
        sys.exit(1)

    md_path = Path(md_file).resolve()
    pdf_path = Path(pdf_file).resolve()

    # Hiragino フォントに存在しない Unicode 記号を LaTeX コマンドに変換
    # \ensuremath は raw_tex 拡張経由でパススルーされ，テキスト/数式どちらでも動作する
    _LATEX_SYMBOL_MAP = {
        "≤": r"\ensuremath{\leq}",
        "≥": r"\ensuremath{\geq}",
        "≠": r"\ensuremath{\neq}",
        "≈": r"\ensuremath{\approx}",
        "⚠": r"\ensuremath{\triangle}",  # 低スコア警告マーク
        "∑": r"\ensuremath{\sum}",
        "∈": r"\ensuremath{\in}",
        "∀": r"\ensuremath{\forall}",
        "∃": r"\ensuremath{\exists}",
    }

    md_content = md_path.read_text(encoding="utf-8")

    # Unicode 記号を LaTeX コマンドに変換
    for sym, latex in _LATEX_SYMBOL_MAP.items():
        md_content = md_content.replace(sym, latex)

    # HTML パスと同じ前処理を適用（LaTeX でも必要）
    # _ensure_hr_spacing: --- の前後に空行を挿入（setext 見出し誤認識防止）
    md_content = _ensure_hr_spacing(md_content)
    # _ensure_list_spacing: リスト前の空行補完
    md_content = _ensure_list_spacing(md_content)
    # テキストフォント非対応の数学記号を LaTeX math コマンドに置換
    md_content = _replace_math_symbols(md_content)
    # 表のセパレータ行ダッシュ数をコンテンツ幅に比例させる（pandoc の \real{} 列幅計算に影響）
    md_content = _adjust_table_column_widths(md_content)
    # 長い ASCII 連続トークン（16進ファイル名等）にゼロ幅スペースを挿入して折り返しを許可
    md_content = _insert_table_long_token_breaks(md_content)
    # CJK 文字間に \hskip0pt を挿入（luatex-ja なし環境での折り返し許可）
    md_content = _insert_cjk_linebreaks(md_content)
    # NOTE: _insert_soft_breaks_for_table_tokens() はマークダウン側では使わない
    # backtick コードスパン内では \hskip0pt がリテラル文字列として表示される。
    # 代わりに .tex ポスト処理で \texttt{} 内に camelCase ブレークを挿入する。
    # PDF リンク記法を画像記法に変換（LaTeX は PDF を直接 \includegraphics できる）
    md_content = _preprocess_pdf_links_for_latex(md_content)

    # LaTeX ヘッダー: 画像幅制限 + テーブルフォントサイズ縮小 + 行間調整 + sloppy
    header_tex = r"""\usepackage{graphicx}
\setkeys{Gin}{width=\linewidth,height=0.8\textheight,keepaspectratio}
\usepackage{etoolbox}
\usepackage{array}
\AtBeginEnvironment{longtable}{%
  \footnotesize\setlength{\tabcolsep}{2pt}\renewcommand{\arraystretch}{1.3}%
  \tolerance=9999\emergencystretch=5em\sloppy%
  \hyphenpenalty=10000\exhyphenpenalty=10000}
\AtBeginEnvironment{tabular}{%
  \footnotesize\setlength{\tabcolsep}{2pt}\renewcommand{\arraystretch}{1.3}%
  \tolerance=9999\emergencystretch=5em\sloppy%
  \hyphenpenalty=10000\exhyphenpenalty=10000}
% CJK テキストの行オーバーフロー防止
\sloppy
\setlength{\emergencystretch}{3em}
"""

    work_dir = md_path.parent
    stem = Path(tempfile.mktemp(suffix="", dir=str(work_dir))).name
    tmp_md = work_dir / f"{stem}.md"
    tex_file = work_dir / f"{stem}.tex"
    header_file = work_dir / f"{stem}_header.tex"

    tmp_md.write_text(md_content, encoding="utf-8")
    header_file.write_text(header_tex, encoding="utf-8")

    success = False
    try:
        # Step 1: pandoc で中間 .tex を生成
        r1 = subprocess.run(
            [
                "pandoc", str(tmp_md),
                "-o", str(tex_file),
                "--pdf-engine=lualatex",
                "-f", "markdown-yaml_metadata_block",
                "-s",  # standalone (.tex として完結)
                "-V", "documentclass=article",
                "-V", "papersize=a4",
                "-V", "fontsize=11pt",
                "-V", "geometry:top=2.5cm,bottom=2.5cm,left=2cm,right=2cm",
                "-V", "mainfont=Hiragino Mincho ProN",
                "-V", "sansfont=Hiragino Sans",
                "-V", "monofont=Menlo",
                "-V", "monofontoptions=Scale=MatchLowercase",
                "-V", "colorlinks=false",
                "--highlight-style=tango",
                "--include-in-header", str(header_file),
                "--resource-path=.",
            ],
            capture_output=True, text=True, cwd=str(work_dir),
        )
        if r1.returncode != 0 or not tex_file.exists():
            print(f"Error: pandoc → .tex 失敗:\n{r1.stderr}", file=sys.stderr)
            sys.exit(1)

        # NOTE: 縦罫線は追加しない（booktabs スタイルで統一）
        tex_content = tex_file.read_text(encoding="utf-8")
        # \texttt{} 内の camelCase に \hskip0pt を挿入して折り返し候補を追加
        tex_content = _tex_add_texttt_breaks(tex_content)
        # 単純 l/c/r 列指定を p{} 幅指定に変換してテキスト折り返しを有効にする
        tex_content = _tex_fix_table_columns(tex_content)
        tex_file.write_text(tex_content, encoding="utf-8")

        # Step 3: lualatex を 2 回実行（相互参照解決）
        for _ in range(2):
            r2 = subprocess.run(
                [
                    "lualatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={work_dir}",
                    str(tex_file),
                ],
                capture_output=True, text=True, cwd=str(work_dir),
            )

        out_pdf = tex_file.with_suffix(".pdf")
        if not out_pdf.exists():
            print(f"Error: lualatex 失敗:\n{r2.stderr[-2000:]}", file=sys.stderr)
            sys.exit(1)

        import shutil as _shutil
        _shutil.copy(str(out_pdf), str(pdf_path))
        success = True

    finally:
        out_pdf_maybe = tex_file.with_suffix(".pdf") if tex_file.exists() else None
        if success:
            # 成功時は全中間ファイルを削除
            targets = [tmp_md, tex_file, header_file,
                       tex_file.with_suffix(".aux"), tex_file.with_suffix(".log"),
                       tex_file.with_suffix(".out"), out_pdf_maybe]
        else:
            # エラー時は .tex と _header.tex を残してデバッグに使えるようにする
            print(f"Debug: 中間ファイルを保持しています: {tex_file}, {header_file}", file=sys.stderr)
            targets = [tmp_md,
                       tex_file.with_suffix(".aux"), tex_file.with_suffix(".log"),
                       tex_file.with_suffix(".out"), out_pdf_maybe]
        for f in targets:
            if f and Path(f).exists():
                Path(f).unlink(missing_ok=True)


def _has_playwright() -> bool:
    """Check if playwright is importable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _render_playwright(full_html: str, pdf_file: str, base_dir: Path | None = None) -> None:
    """Render PDF using Playwright (CDP Page.printToPDF).

    Chrome の --print-to-pdf とは異なり，CDP 経由で margins を正確に指定するため
    テーブル右罫線のクリップが発生しない．
    base_dir を指定すると HTML をその場所に保存し，相対パスの画像が解決される．
    """
    from playwright.sync_api import sync_playwright

    if base_dir and base_dir.resolve().is_dir():
        html_path = base_dir.resolve() / ".tmp_pdf_render.html"
    else:
        html_path = Path(tempfile.mktemp(suffix=".html"))

    html_path.write_text(full_html, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(f"file://{html_path}", wait_until="networkidle")
            page.wait_for_timeout(500)
            page.pdf(
                path=pdf_file,
                format="A4",
                margin={"top": "2.5cm", "bottom": "2.5cm", "left": "2cm", "right": "2.3cm"},
                print_background=True,
                display_header_footer=False,
            )
            browser.close()
    finally:
        html_path.unlink(missing_ok=True)


def _detect_backend() -> str:
    """Auto-detect best available backend: lualatex > playwright > weasyprint > chrome."""
    if _has_lualatex():
        return "lualatex"
    if _has_playwright():
        return "playwright"
    if _has_weasyprint():
        return "weasyprint"
    if _find_chrome():
        return "chrome"
    print(
        "Error: No PDF backend found.\n"
        "  lualatex: requires lualatex + pandoc (recommended)\n"
        "  playwright: uv run --with playwright playwright install chromium\n"
        "  weasyprint: pip install weasyprint (+ libpango)\n"
        "  chrome: install Google Chrome",
        file=sys.stderr,
    )
    sys.exit(1)


def _render_weasyprint(full_html: str, pdf_file: str, css: str) -> None:
    """Render PDF using weasyprint."""
    from weasyprint import CSS, HTML

    HTML(string=full_html).write_pdf(pdf_file, stylesheets=[CSS(string=css)])


def _render_chrome(full_html: str, pdf_file: str, base_dir: Path | None = None) -> None:
    """Render PDF using headless Chrome.

    base_dir を指定すると HTML をその場所に保存するため，相対パスの画像が解決される.
    """
    chrome = _find_chrome()
    if not chrome:
        print("Error: Chrome not found.", file=sys.stderr)
        sys.exit(1)

    # 画像の相対パスを解決するため，markdown と同じディレクトリに HTML を保存する
    if base_dir and base_dir.resolve().is_dir():
        html_path = str(base_dir.resolve() / ".tmp_pdf_render.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(full_html)
    else:
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(full_html)
            html_path = f.name

    try:
        result = subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=5000",
                "--window-size=794,1123",  # A4 at 96dpi (210mm×297mm)
                f"--print-to-pdf={pdf_file}",
                f"file://{html_path}",
            ],
            capture_output=True,
            text=True,
        )
        if not Path(pdf_file).exists():
            print(
                f"Error: Chrome failed to generate PDF. stderr: {result.stderr}",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        Path(html_path).unlink(missing_ok=True)


def markdown_to_pdf(
    md_file: str,
    pdf_file: str | None = None,
    theme: str = "default",
    backend: str | None = None,
) -> str:
    """
    Convert markdown file to PDF.

    Args:
        md_file: Path to input markdown file
        pdf_file: Path to output PDF (optional, defaults to same name as input)
        theme: Theme name (from themes/ directory)
        backend: 'weasyprint', 'chrome', or None (auto-detect)

    Returns:
        Path to generated PDF file
    """
    md_path = Path(md_file)
    if pdf_file is None:
        pdf_file = str(md_path.with_suffix(".pdf"))

    if backend is None:
        backend = _detect_backend()

    css = _load_theme(theme)
    html_content = _md_to_html(md_file)
    full_html = _build_full_html(html_content, css, md_path.stem)

    if backend == "lualatex":
        # lualatex は HTML パイプラインを使わず pandoc が直接 PDF を生成する
        _render_lualatex(md_file, pdf_file)
    elif backend == "playwright":
        _render_playwright(full_html, pdf_file, md_path.parent)
    elif backend == "weasyprint":
        _render_weasyprint(full_html, pdf_file, css)
    elif backend == "chrome":
        _render_chrome(full_html, pdf_file, md_path.parent)
    else:
        print(f"Error: Unknown backend '{backend}'", file=sys.stderr)
        sys.exit(1)

    size_kb = Path(pdf_file).stat().st_size / 1024
    print(f"Generated: {pdf_file} ({size_kb:.0f}KB, theme={theme}, backend={backend})")
    return pdf_file


def main():
    available_themes = _list_themes()

    parser = argparse.ArgumentParser(
        description="Markdown to PDF with Chinese font support and themes."
    )
    parser.add_argument("input", help="Input markdown file")
    parser.add_argument("output", nargs="?", help="Output PDF file (optional)")
    parser.add_argument(
        "--theme",
        default="default",
        choices=available_themes or ["default"],
        help=f"CSS theme (available: {', '.join(available_themes) or 'default'})",
    )
    parser.add_argument(
        "--backend",
        choices=["lualatex", "playwright", "weasyprint", "chrome"],
        default=None,
        help="PDF rendering backend (default: auto-detect)",
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        help="List available themes and exit",
    )

    args = parser.parse_args()

    if args.list_themes:
        for t in available_themes:
            marker = " (default)" if t == "default" else ""
            css_file = THEMES_DIR / f"{t}.css"
            first_line = ""
            for line in css_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("*") and "—" in line:
                    first_line = line.lstrip("* ").strip()
                    break
            print(f"  {t}{marker}: {first_line}")
        sys.exit(0)

    if not Path(args.input).exists():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    markdown_to_pdf(args.input, args.output, args.theme, args.backend)


if __name__ == "__main__":
    main()
