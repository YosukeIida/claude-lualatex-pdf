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

# 目次
toc: true
toc-own-page: true

# ヘッダ/フッタ
header-left: "プロジェクト名"
header-right: "2026-04-22"
footer-center: "社外秘"

# コードブロック（LaTeX コマンドはシングルクォートで書く）
listings: true
code-block-font-size: '\footnotesize'
---
```

詳細な変数一覧: https://github.com/Wandmalfarbe/pandoc-latex-template

### YAML frontmatter の注意事項

**LaTeX コマンドはシングルクォートを使う**

```yaml
# ✅ 正しい（シングルクォート内はエスケープなし）
code-block-font-size: '\footnotesize'

# ❌ NG（ダブルクォート内の \f が未知のエスケープとして pandoc に拒否される）
code-block-font-size: "\\footnotesize"
```

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
| 長トークン分割 | 16 進ファイル名等の長い ASCII 文字列にゼロ幅スペースを挿入（LaTeX コマンドは除外） |
| CJK バッククォート除去 | 表・段落・リスト内で日本語を含むコードスパンのバッククォートを除去してプロポーショナルフォントで描画 |
| PDF 画像挿入 | `[text](path.pdf)` を `\includegraphics` に変換（リンクテキストがファイルパスの場合は除外） |
| 表オーバーフロー防止 | `l/c/r` 列を `p{}` 幅指定に変換して折り返しを有効化 |
| frontmatter 保護 | YAML frontmatter を本文と分離し，LaTeX 変換の影響を受けないよう保護 |
| エラー時 .tex 保持 | 失敗時のみ `.tex`・ヘッダファイルを保持してデバッグを可能にする |

### Markdown の書き方：自動処理される内容の詳細

**日本語をコードスパンで囲む必要はない**

Inconsolata（等幅フォント）は CJK 非対応のため，バッククォートで囲んだ日本語は豆腐（□）になる．
スクリプトが自動的にバッククォートを除去するが，そもそも書かないのが最善．

```markdown
<!-- ✅ 推奨：バッククォートなし -->
| 2024年 2月ご請求分 | [] | 2024年 2月ご請求分 |

<!-- ⚠ 自動修正される（CJK を含むバッククォートは除去される） -->
| `2024年 2月ご請求分` | `[]` | `2024年 2月ご請求分` |

<!-- ✅ OK：ASCII のみのコードスパンはそのまま -->
| `gemini-2.5-flash` | `0.563` | `0.915` |
```

**チェックリスト内のファイルパスリンクは PDF 埋め込みにならない**

リンクテキストが `/` を含む場合（ファイルパス）は画像埋め込み変換をスキップする．

```markdown
<!-- ✅ ファイルパスリンク → そのままリンクとして出力 -->
- [ ] [data/raw/invoice/images/0010.jpg](../../../../data/raw/invoice/images/0010.jpg)

<!-- ✅ 図の埋め込み → \includegraphics に変換（リンクテキストがファイルパスでない場合） -->
[Figure 1](output/figure1.pdf)
```

**≥ ≤ 等の記号は Unicode で書く**

Markdown 中に `≥` `≤` `≠` をそのまま書けば，スクリプトが `\ensuremath{\geq}` 等に変換する．
`$x \geq 0$` のような LaTeX 数式記法も使用可能．

## Requirements

Docker Desktop がインストールされていれば動作する（pandoc・lualatex のローカルインストール不要）．

初回実行時に `texlive/texlive:latest`（arm64 native）をベースにした専用イメージ
`claudeskill-lualatex-pdf:latest` を自動ビルドする（数分かかる）．
以降はキャッシュされるため即時起動する．

`md_to_pdf.py` は標準ライブラリのみで動作するため Python 依存も不要．

## Troubleshooting

**日本語が豆腐（□）**: `claudeskill-lualatex-pdf:latest` イメージが正しくビルドされているか確認．
`docker run --rm claudeskill-lualatex-pdf:latest fc-list | grep IPAex` でフォントを確認する

**表がはみ出す**: pandoc 3.7+ が必要（自動ビルドイメージに含まれる pandoc のバージョンを確認）

**Docker が見つからない**: `which docker` で確認．Docker Desktop が起動しているか確認

**イメージの再ビルドが必要な場合**: `docker rmi claudeskill-lualatex-pdf:latest` で削除すると次回実行時に再ビルドされる

**`pagecolor.sty not found`**: `texlive/texlive:latest` は TeX Live フルインストールなので発生しないはず

**YAML parse error（unknown escape character）**: frontmatter で LaTeX コマンドをダブルクォートで書いている場合に発生．シングルクォートに変更する（`'\footnotesize'`）

**`\ensuremath{\geq}` 等が崩れて表示される**: この問題は修正済み（LaTeX コマンドを含むトークンはゼロ幅スペース分割から除外）．発生する場合はスクリプトのバージョンを確認

**チェックリスト内のファイルが PDF として埋め込まれる**: この問題は修正済み（リンクテキストにファイルパスを含む場合は画像変換をスキップ）．発生する場合はスクリプトのバージョンを確認

## License / Attribution

`eisvogel.latex` は [Eisvogel](https://github.com/Wandmalfarbe/pandoc-latex-template)
（Pascal Wagler, John MacFarlane 著）を同梱しています．

> Copyright (c) 2017 - 2026, Pascal Wagler  
> Copyright (c) 2014 - 2026, John MacFarlane  
> BSD 3-Clause License

ライセンス全文はファイルヘッダー（`eisvogel.latex` 冒頭 1〜34 行）を参照してください．
`md_to_pdf.py` および Eisvogel への追加パッチ部分はこのリポジトリのライセンスに従います．
