---
name: lualatex-pdf
description: Convert Markdown to PDF using LuaLaTeX with Japanese/CJK support, automatic table column width adjustment, and long token line-break handling. Triggers on "PDF作って", "PDF出力", "generate PDF", "markdown to PDF", or any request to create a printable document.
---

# LuaLaTeX PDF Creator

Markdown から PDF を生成する Claude Code プラグイン．
**pandoc + LuaLaTeX + Eisvogel テンプレート** をベースにした，日本語・CJK 文書向け変換パイプライン．

## Quick Start

```bash
python md_to_pdf.py input.md
python md_to_pdf.py input.md output.pdf
```

## Eisvogel 設定（YAML frontmatter）

文書スタイルは Markdown ファイルの YAML frontmatter で制御する:

```yaml
---
title: ドキュメントタイトル
author: 著者名
date: 2026-04-22
lang: ja

# タイトルページ
titlepage: true
titlepage-color: "1E4C7A"
titlepage-text-color: "FFFFFF"
titlepage-rule-color: "FFFFFF"

# 目次
toc: true
toc-own-page: true

# ヘッダ/フッタ
header-left: "プロジェクト名"
header-right: "\\thepage"
footer-center: "社外秘"

# コードブロック
listings: true
code-block-font-size: "\\footnotesize"
---
```

詳細な変数一覧: https://github.com/Wandmalfarbe/pandoc-latex-template

## ファイル構成

```
lualatex-pdf/
├── SKILL.md
├── eisvogel.latex    # Eisvogel テンプレート（v3.4.0）
└── md_to_pdf.py      # 変換スクリプト
```

## 自動処理される内容

| 処理 | 説明 |
|------|------|
| 数式記号変換 | `≤ ≥ ≠ ≈ ∑ ∈` 等を LaTeX コマンドに自動変換 |
| CJK 折り返し | CJK 文字間に `\hskip0pt` を挿入して任意位置での折り返しを許可 |
| 表列幅最適化 | セパレータ行のダッシュ数をコンテンツ幅に比例させ列幅を自動調整 |
| 長トークン分割 | 16 進ファイル名等の長い ASCII 文字列にゼロ幅スペースを挿入 |
| PDF 画像挿入 | `[text](path.pdf)` を `\includegraphics` に変換（PNG 変換不要） |
| 表オーバーフロー防止 | `l/c/r` 列を `p{}` 幅指定に変換して折り返しを有効化 |
| エラー時 .tex 保持 | 失敗時のみ `.tex`・ヘッダファイルを保持してデバッグを可能にする |

## Requirements

```bash
brew install pandoc
brew install --cask mactex-no-gui   # または basictex
```

## Troubleshooting

**日本語が豆腐（□）**: Hiragino フォントが必要（macOS 標準搭載）

**表がはみ出す**: pandoc 3.7+ 必須（`brew upgrade pandoc`）

**lualatex が見つからない**: `which lualatex` で確認，なければ MacTeX をインストール

## License / Attribution

`eisvogel.latex` は [Eisvogel](https://github.com/Wandmalfarbe/pandoc-latex-template)
（Pascal Wagler, John MacFarlane 著）を同梱しています．

> Copyright (c) 2017 - 2026, Pascal Wagler  
> Copyright (c) 2014 - 2026, John MacFarlane  
> BSD 3-Clause License

ライセンス全文はファイルヘッダー（`eisvogel.latex` 冒頭 1〜34 行）を参照してください．
`md_to_pdf.py` および Eisvogel への追加パッチ部分はこのリポジトリのライセンスに従います．
