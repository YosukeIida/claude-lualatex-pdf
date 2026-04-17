---
name: lualatex-pdf
description: Convert Markdown to PDF using LuaLaTeX with Japanese/CJK support, automatic table column width adjustment, and long token line-break handling. Triggers on "PDF作って", "PDF出力", "generate PDF", "markdown to PDF", or any request to create a printable document.
---

# LuaLaTeX PDF Creator

Markdown から LuaLaTeX 経由で PDF を生成する Claude Code プラグイン．日本語・CJK 文書向けに最適化されています．

## Quick Start

```bash
# デフォルトテーマ
python scripts/md_to_pdf.py input.md output.pdf

# テーマ指定
python scripts/md_to_pdf.py input.md output.pdf --theme warm-terra

# バックエンド指定
python scripts/md_to_pdf.py input.md output.pdf --backend lualatex
python scripts/md_to_pdf.py input.md output.pdf --backend chrome

# テーマ一覧
python scripts/md_to_pdf.py --list-themes dummy.md
```

## Backends

| Backend | 必要なもの | 特徴 |
|---------|-----------|------|
| `lualatex` | pandoc + lualatex | 日本語・表レイアウト最適，**デフォルト** |
| `chrome` | Google Chrome | 手軽，CJK フォント依存 |
| `weasyprint` | pip install weasyprint | CSS 忠実，CJK 部分対応 |

## Themes

| テーマ | フォント | 用途 |
|--------|---------|------|
| `default` | Songti SC / Heiti SC | 公式文書・レポート（黒/グレー） |
| `warm-terra` | PingFang SC | 研修資料・ワークショップ（テラコッタ） |

## Features

- **LuaLaTeX バックエンド**: pandoc 経由で .tex 生成 → lualatex でコンパイル
- **日本語折り返し**: CJK 文字間に `\hskip0pt` を自動挿入（backtick スパン内はスキップ）
- **表列幅自動調整**: セパレータ行のダッシュ数をコンテンツ幅に比例させ pandoc の `\real{}` 列幅計算を制御
- **長 ASCII トークン分割**: 16 進ファイル名等に U+200B を 8 文字ごとに挿入して折り返しを許可（表セル・段落中のバッククォートスパン両対応）
- **エラー時のみ .tex 保持**: 成功時は中間ファイルを自動削除，失敗時はデバッグ用に保持

## Troubleshooting

**lualatex が見つからない**: `brew install --cask mactex-no-gui` または `brew install basictex`

**日本語が豆腐（□）**: Songti SC / Hiragino フォントが必要（macOS 標準搭載）

**表がはみ出す**: pandoc 3.7+ 必須（`brew upgrade pandoc`）
