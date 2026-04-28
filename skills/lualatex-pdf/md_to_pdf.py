#!/usr/bin/env python3
"""Markdown から PDF を生成する（pandoc + LuaLaTeX + Eisvogel テンプレート）.

Usage:
    python md_to_pdf.py input.md [output.pdf]

Eisvogel の各種設定（タイトルページ・ヘッダ/フッタ等）は
Markdown ファイルの YAML frontmatter で制御する:

    ---
    title: ドキュメントタイトル
    author: 著者名
    titlepage: true
    toc: true
    lang: ja
    ---

Requirements:
    pandoc (brew install pandoc)
    lualatex (brew install --cask mactex-no-gui)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = PLUGIN_DIR / "eisvogel.latex"

# Hiragino フォントが持たない Unicode 記号を LaTeX コマンドに変換
_LATEX_SYMBOL_MAP = {
    "≤": r"\ensuremath{\leq}",
    "≥": r"\ensuremath{\geq}",
    "≠": r"\ensuremath{\neq}",
    "≈": r"\ensuremath{\approx}",
    "⚠": r"\ensuremath{\triangle}",
    "∑": r"\ensuremath{\sum}",
    "∈": r"\ensuremath{\in}",
    "∀": r"\ensuremath{\forall}",
    "∃": r"\ensuremath{\exists}",
}

# 表サイズ・折り返し調整ヘッダー（Eisvogel の longtable スタイルを補完）
_TABLE_HEADER_TEX = r"""\usepackage{graphicx}
\setkeys{Gin}{width=\linewidth,height=0.8\textheight,keepaspectratio}
\usepackage{array}
\usepackage{etoolbox}
\AtBeginEnvironment{longtable}{%
  \footnotesize\setlength{\tabcolsep}{2pt}\renewcommand{\arraystretch}{1.3}%
  \tolerance=9999\emergencystretch=5em\sloppy%
  \hyphenpenalty=10000\exhyphenpenalty=10000}
\AtBeginEnvironment{tabular}{%
  \footnotesize\setlength{\tabcolsep}{2pt}\renewcommand{\arraystretch}{1.3}%
  \tolerance=9999\emergencystretch=5em\sloppy%
  \hyphenpenalty=10000\exhyphenpenalty=10000}
\sloppy
\setlength{\emergencystretch}{3em}
"""


# ─── Markdown 前処理 ──────────────────────────────────────────────────────────

def _split_frontmatter(md: str) -> tuple[str, str]:
    """YAML frontmatter と本文を分割して返す.

    冒頭が `---` で始まり，次の `---` または `...` で終わるブロックを
    frontmatter として扱う．frontmatter がなければ ("", md) を返す．
    """
    lines = md.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", md
    for j in range(1, len(lines)):
        if lines[j].strip() in ("---", "..."):
            frontmatter = "\n".join(lines[: j + 1]) + "\n"
            body = "\n".join(lines[j + 1 :])
            return frontmatter, body
    return "", md


def _ensure_hr_spacing(text: str) -> str:
    """--- の前後に空行を挿入する（setext 見出し誤認識防止）.

    本文のみを受け取る前提（frontmatter は _split_frontmatter で除去済み）．
    """
    lines = text.split("\n")
    result: list[str] = []
    hr_re = re.compile(r"^---+$")
    for i, line in enumerate(lines):
        if hr_re.match(line):
            if result and result[-1].strip():
                result.append("")
            result.append(line)
            if i + 1 < len(lines) and lines[i + 1].strip():
                result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def _ensure_list_spacing(text: str) -> str:
    """リスト前の空行を補完する（pandoc がリストを段落として扱うのを防ぐ）."""
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


def _adjust_table_column_widths(md: str) -> str:
    """セパレータ行のダッシュ数をコンテンツ幅に比例させる.

    pandoc はセパレータ行の --- 数を列幅比率（\\real{}）として解釈するため,
    各列の最大コンテンツ幅（CJK=2, ASCII=1）に比例したダッシュ数を設定する.
    """
    def _char_width(s: str) -> int:
        return sum(2 if ord(c) > 0x2E7F else 1 for c in s)

    lines = md.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
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

            col_max: list[int] = []
            for row in [header_line] + body_lines:
                cells = [c.strip() for c in row.split("|")[1:-1]]
                for ci, cell in enumerate(cells):
                    w = _char_width(cell)
                    if ci < len(col_max):
                        col_max[ci] = max(col_max[ci], w)
                    else:
                        col_max.append(w)

            MAX_TEXT = 20
            MIN_DATA = 12
            LONG_THRESHOLD = 15
            capped: list[int] = []
            for w in col_max:
                if w > LONG_THRESHOLD:
                    capped.append(min(w, MAX_TEXT))
                else:
                    capped.append(max(w, MIN_DATA))

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


def _insert_table_long_token_breaks(md: str) -> str:
    """長い ASCII 連続文字列にゼロ幅スペース（U+200B）を挿入する.

    16進ファイル名等の区切り文字のない長いトークンは LaTeX が折り返せないため,
    8 文字ごとにゼロ幅スペースを挿入して折り返し候補を追加する.
    表セルと段落中のバッククォートスパン内が対象.
    """
    ZWSP = "​"
    CHUNK = 8
    MIN_LEN = 16

    def _break_token(token: str) -> str:
        if len(token) < MIN_LEN:
            return token
        if any(ord(c) > 0x2E7F for c in token):
            return token
        if "\\" in token:
            return token  # LaTeX コマンド（\ensuremath 等）は分割しない
        return ZWSP.join(token[i:i + CHUNK] for i in range(0, len(token), CHUNK))

    def _process_text(text: str) -> str:
        return " ".join(_break_token(t) for t in text.split(" "))

    def _process_backtick_spans(line: str) -> str:
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
            parts = line.split("|")
            new_parts = [parts[0]] + [_process_text(p) for p in parts[1:-1]] + [parts[-1]]
            result_lines.append("|".join(new_parts))
        elif stripped and not stripped.startswith("#") and "`" in line:
            result_lines.append(_process_backtick_spans(line))
        else:
            result_lines.append(line)
    return "\n".join(result_lines)


def _insert_cjk_linebreaks(text: str) -> str:
    r"""CJK 文字間に \hskip0pt を挿入して折り返し候補を追加する.

    luatexja なし環境では CJK 文字列が 1 語として扱われ p{} 列をはみ出す.
    ゼロ幅スキップを挿入することで任意位置での折り返しを許可する.
    backtick コードスパン内はスキップ（\hskip0pt がリテラル文字列になるため）.
    """
    def _process_segment(seg: str) -> str:
        result: list[str] = []
        prev_cjk = False
        for ch in seg:
            cp = ord(ch)
            is_cjk = (
                0x3000 <= cp <= 0x9FFF
                or 0xF900 <= cp <= 0xFAFF
                or 0xFF00 <= cp <= 0xFFEF
            )
            if is_cjk and prev_cjk:
                result.append(r"\hskip0pt ")
            result.append(ch)
            prev_cjk = is_cjk
        return "".join(result)

    segments = re.split(r"(`[^`\n]+`)", text)
    return "".join(
        seg if idx % 2 == 1 else _process_segment(seg)
        for idx, seg in enumerate(segments)
    )


def _strip_cjk_backticks(md: str) -> str:
    """CJK 文字を含むバッククォートスパンのバッククォートを全コンテキストで除去する.

    Menlo 等の等幅フォントは CJK 非対応のため，日本語を含むコードスパンが
    あると豆腐になる．バッククォートを除去してプロポーショナルフォント
    （Hiragino 等）でレンダリングさせることで豆腐を防ぐ．

    本文のみを受け取る前提（frontmatter は _split_frontmatter で除去済み）．
    表のセパレータ行（|---|---|）はスキップする．
    """
    def _has_cjk(s: str) -> bool:
        return any(
            0x3000 <= ord(c) <= 0x9FFF
            or 0xF900 <= ord(c) <= 0xFAFF
            or 0xFF00 <= ord(c) <= 0xFFEF
            for c in s
        )

    def _strip_if_cjk(m: re.Match) -> str:
        content = m.group(1)
        return content if _has_cjk(content) else m.group(0)

    result: list[str] = []
    for line in md.split("\n"):
        if re.match(r"\s*\|[-:\s|]+\|\s*$", line.strip()):
            result.append(line)
        else:
            result.append(re.sub(r"`([^`\n]+)`", _strip_if_cjk, line))
    return "\n".join(result)


def _preprocess_pdf_links_for_latex(text: str) -> str:
    """[text](path.pdf) → ![text](path.pdf) に変換する.

    LaTeX は \\includegraphics で PDF を直接埋め込めるため PNG 変換は不要.
    リモート URL とファイルパスリンク（テキストに / を含む）はスキップする．
    ファイルパスリンクは図の埋め込みではなくナビゲーション用リンクのため除外する．
    """
    link_re = re.compile(r"(?<!!)\[([^\]]*)\]\((.+?\.pdf)\)", re.IGNORECASE)

    def _to_image(m: re.Match) -> str:
        alt, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://")):
            return m.group(0)
        # リンクテキストがファイルパス（/ を含む）の場合はナビゲーション用リンクとみなしスキップ
        if "/" in alt or "\\" in alt:
            return m.group(0)
        return f"![{alt}]({path})"

    return link_re.sub(_to_image, text)


# ─── .tex ポスト処理 ──────────────────────────────────────────────────────────

def _tex_add_texttt_breaks(tex: str) -> str:
    r"""longtable 内の \texttt{} に camelCase 折り返し候補を挿入する.

    \texttt{storeNameBranch} → \texttt{store}\hskip0pt\texttt{NameBranch}
    """
    def _break_camel(m: re.Match) -> str:
        content = m.group(1)

        def _insert_break(m2: re.Match) -> str:
            return m2.group(1) + r"}\hskip0pt\texttt{" + m2.group(2)

        broken = re.sub(r"([a-z])([A-Z])", _insert_break, content)
        if broken == content:
            return m.group(0)
        return r"\texttt{" + broken + "}"

    return re.sub(r"\\texttt\{([^{}]+)\}", _break_camel, tex)


def _tex_fix_table_columns(tex: str) -> str:
    r"""longtable の単純 l/c/r 列指定を p{} 幅指定に変換する.

    pandoc が生成する @{}lll...@{} 形式はテキスト折り返しを行わないため,
    l 列を >{\raggedright}p{Xcm} 等に変換してセル内折り返しを有効にする.
    \real{} や \columnwidth を含む既計算済み指定はスキップする.
    """
    PAGE_WIDTH_CM = 17.0
    TABCOLSEP_CM = 0.07
    SHORT_COL_CM = 1.40

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


# ─── フォント DB ──────────────────────────────────────────────────────────────

def _writable_texmfvar() -> Path | None:
    """書き込み可能な TEXMFVAR を返す.

    デフォルトの TEXMFVAR が書き込める環境（MacTeX 等）では None を返し，
    既存の設定をそのまま使う．Nix 等で書き込めない場合は $TMPDIR 下の
    固定パスを返す．
    """
    existing = os.environ.get("TEXMFVAR", "")
    if existing:
        p = Path(existing)
        if p.exists() and os.access(p, os.W_OK):
            return None
    fallback = Path(tempfile.gettempdir()) / "claude-lualatex-texmf-var"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _ensure_luaotfload_cache(texmf_var: Path) -> None:
    """フォント DB が未構築なら luaotfload-tool で構築する.

    同一セッション内では再構築しない（DB ファイルが存在すればスキップ）．
    """
    names_dir = texmf_var / "luatex-cache" / "generic" / "names"
    if names_dir.exists() and any(names_dir.glob("luaotfload-names*.luc")):
        return
    env = {**os.environ, "TEXMFVAR": str(texmf_var)}
    subprocess.run(
        ["luaotfload-tool", "--update", "--force"],
        env=env,
        capture_output=True,
        timeout=120,
    )


# ─── レンダリング ─────────────────────────────────────────────────────────────

def _render(md_file: str, pdf_file: str) -> None:
    """pandoc + LuaLaTeX で PDF を生成する（Eisvogel テンプレート使用）."""
    if not shutil.which("pandoc"):
        print(
            "Error: pandoc が見つかりません。\n"
            "  brew install pandoc でインストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)
    if not shutil.which("lualatex"):
        print(
            "Error: lualatex が見つかりません。\n"
            "  brew install --cask mactex-no-gui でインストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)
    if not TEMPLATE_PATH.exists():
        print(
            f"Error: Eisvogel テンプレートが見つかりません: {TEMPLATE_PATH}\n"
            "  templates/eisvogel.latex が正しく配置されているか確認してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    md_path = Path(md_file).resolve()
    pdf_path = Path(pdf_file).resolve()
    work_dir = md_path.parent

    # Markdown 前処理
    # YAML frontmatter を本文と分離し，本文のみに各変換を適用する．
    # frontmatter に LaTeX コマンドや空行が混入すると pandoc の YAML パーサーが失敗するため．
    md_content = md_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(md_content)
    for sym, latex in _LATEX_SYMBOL_MAP.items():
        body = body.replace(sym, latex)
    body = _ensure_hr_spacing(body)
    body = _ensure_list_spacing(body)
    body = _adjust_table_column_widths(body)
    body = _insert_table_long_token_breaks(body)
    body = _strip_cjk_backticks(body)
    body = _insert_cjk_linebreaks(body)
    body = _preprocess_pdf_links_for_latex(body)
    md_content = frontmatter + body

    # 一時ファイルを work_dir に作成（pandoc の --resource-path が機能するよう）
    stem = Path(tempfile.mktemp(suffix="", dir=str(work_dir))).name
    tmp_md = work_dir / f"{stem}.md"
    tex_file = work_dir / f"{stem}.tex"
    header_file = work_dir / f"{stem}_header.tex"

    tmp_md.write_text(md_content, encoding="utf-8")
    header_file.write_text(_TABLE_HEADER_TEX, encoding="utf-8")

    success = False
    try:
        # Step 1: pandoc で中間 .tex を生成
        r1 = subprocess.run(
            [
                "pandoc", str(tmp_md),
                "-o", str(tex_file),
                "--pdf-engine=lualatex",
                "-f", "markdown",
                "-s",
                "--template", str(TEMPLATE_PATH),
                "-V", "mainfont=Hiragino Mincho ProN",
                "-V", "sansfont=Hiragino Sans",
                "-V", "monofont=Menlo",
                "-V", "monofontoptions=Scale=MatchLowercase",
                "-V", "footnotes-disable-backlinks=true",
                "--highlight-style=tango",
                "--include-in-header", str(header_file),
                "--resource-path=.",
            ],
            capture_output=True, text=True, cwd=str(work_dir),
        )
        if r1.returncode != 0 or not tex_file.exists():
            print(f"Error: pandoc → .tex 失敗:\n{r1.stderr}", file=sys.stderr)
            sys.exit(1)

        # Step 2: .tex ポスト処理
        tex_content = tex_file.read_text(encoding="utf-8")
        tex_content = _tex_add_texttt_breaks(tex_content)
        tex_content = _tex_fix_table_columns(tex_content)
        tex_file.write_text(tex_content, encoding="utf-8")

        # Step 3: lualatex を 2 回実行（相互参照解決）
        # Nix 等で fontconfig のキャッシュ書き込み先がない場合に備え，
        # TEXMFVAR を書き込み可能なディレクトリに向けてフォント DB を確保する．
        texmf_var = _writable_texmfvar()
        lualatex_env = os.environ.copy()
        if texmf_var is not None:
            _ensure_luaotfload_cache(texmf_var)
            lualatex_env["TEXMFVAR"] = str(texmf_var)

        for _ in range(2):
            r2 = subprocess.run(
                [
                    "lualatex",
                    "-interaction=nonstopmode",
                    f"-output-directory={work_dir}",
                    str(tex_file),
                ],
                capture_output=True, text=True, cwd=str(work_dir),
                env=lualatex_env,
            )

        out_pdf = tex_file.with_suffix(".pdf")
        if not out_pdf.exists():
            print(f"Error: lualatex 失敗:\n{r2.stderr[-2000:]}", file=sys.stderr)
            sys.exit(1)

        shutil.copy(str(out_pdf), str(pdf_path))
        success = True

    finally:
        out_pdf_maybe = tex_file.with_suffix(".pdf") if tex_file.exists() else None
        if success:
            targets = [
                tmp_md, tex_file, header_file,
                tex_file.with_suffix(".aux"),
                tex_file.with_suffix(".log"),
                tex_file.with_suffix(".out"),
                out_pdf_maybe,
            ]
        else:
            print(f"Debug: 中間ファイルを保持しています: {tex_file}, {header_file}", file=sys.stderr)
            targets = [
                tmp_md,
                tex_file.with_suffix(".aux"),
                tex_file.with_suffix(".log"),
                tex_file.with_suffix(".out"),
                out_pdf_maybe,
            ]
        for f in targets:
            if f and Path(f).exists():
                Path(f).unlink(missing_ok=True)


def markdown_to_pdf(md_file: str, pdf_file: str | None = None) -> str:
    """Markdown ファイルを PDF に変換して出力パスを返す."""
    md_path = Path(md_file)
    if not md_path.exists():
        print(f"Error: ファイルが見つかりません: {md_file}", file=sys.stderr)
        sys.exit(1)
    if pdf_file is None:
        pdf_file = str(md_path.with_suffix(".pdf"))

    _render(md_file, pdf_file)
    size_kb = Path(pdf_file).stat().st_size / 1024
    print(f"Generated: {pdf_file} ({size_kb:.0f}KB)")
    return pdf_file


def main():
    parser = argparse.ArgumentParser(
        description="Markdown → PDF 変換（pandoc + LuaLaTeX + Eisvogel テンプレート）",
        epilog=(
            "Eisvogel の各種設定は Markdown の YAML frontmatter で制御します:\n"
            "  title, author, date, titlepage, toc, lang, header-left/right, ... など\n"
            "詳細: https://github.com/Wandmalfarbe/pandoc-latex-template"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="入力 Markdown ファイル")
    parser.add_argument("output", nargs="?", help="出力 PDF ファイル（省略時は入力ファイルと同名）")
    args = parser.parse_args()

    markdown_to_pdf(args.input, args.output)


if __name__ == "__main__":
    main()
