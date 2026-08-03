# claude-lualatex-pdf

Markdown から PDF を生成する Claude Code plugin です。

`pandoc + LuaLaTeX + Eisvogel` を使い、日本語/CJK 文書、表の折り返し、長い ASCII トークンの分割、PDF 画像挿入に対応します。

## Requirements

この plugin は次のコマンドが `PATH` 上にあることを前提にします。

```bash
pandoc
lualatex
```

`latexmk` は直接は呼びませんが、TeX 環境の確認や手動デバッグで使うことがあります。

## Nix / direnv

Nix devshell で使う場合は、対象 repo の `nix/flake.nix` の `packages` に次を追加します。

```nix
packages = [
  python
  uv
  nodejs
  pkgs.pandoc
  pkgs.texlive.combined.scheme-medium
];
```

`.envrc` は次のようにして、direnv で有効化します。

```bash
use flake path:./nix
```

```bash
direnv allow
```

`pkgs.texlive.combined.scheme-medium` には `lualatex`, `latexmk`, `tlmgr` が含まれます。Eisvogel テンプレートで追加パッケージ不足が出る場合は、まず `pkgs.texlive.combined.scheme-full` に切り替えて問題の切り分けをしてください。

## Homebrew

Homebrew でグローバルに入れる場合は次を使います。

```bash
brew install pandoc
brew install --cask mactex-no-gui
```

軽量にしたい場合は `basictex` でも始められます。

```bash
brew install pandoc
brew install --cask basictex
```

ただし `basictex` は最小構成なので、Eisvogel が要求する LaTeX パッケージが不足する場合があります。安定して使うなら `mactex-no-gui` を推奨します。

## Usage

```bash
python skills/lualatex-pdf/md_to_pdf.py input.md
python skills/lualatex-pdf/md_to_pdf.py input.md output.pdf
```

Markdown の YAML frontmatter でタイトルページ、目次、ヘッダ/フッタなどを制御できます。

```yaml
---
title: ドキュメントタイトル
author: 著者名
date: 2026-04-22
lang: ja
titlepage: true
toc: true
toc-own-page: true
---
```

## Troubleshooting

### `pandoc` が見つからない

```bash
which pandoc
pandoc -v
```

Nix/devshell 運用の場合は、対象 repo で `direnv allow` 済みか、現在の shell が devshell 内かを確認してください。

### `lualatex` が見つからない

```bash
which lualatex
lualatex --version
```

Nix なら `pkgs.texlive.combined.scheme-medium`、Homebrew なら `mactex-no-gui` または `basictex` が必要です。

### 日本語が豆腐になる

この plugin は macOS 標準の Hiragino フォントを前提にしています。macOS 以外で使う場合は `md_to_pdf.py` の `mainfont`, `sansfont`, `monofont` 指定を環境に合わせて変更してください。

## License / Attribution

`skills/lualatex-pdf/eisvogel.latex` は [Eisvogel](https://github.com/Wandmalfarbe/pandoc-latex-template) を同梱しています。

Copyright and license details are included in the template header.
