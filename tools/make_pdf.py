"""Erzeugt docs/TiLaser-Bedienungsanleitung.pdf aus docs/Bedienungsanleitung.md.

    python tools/make_pdf.py [ausgabe.pdf]

Benötigt: pandoc und XeLaTeX (TeX Live mit KOMA-Script; für deutsche Silbentrennung
zusätzlich das Paket texlive-lang-german). Fehlende Screenshots werden als Platzhalter gesetzt.
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "docs", "Bedienungsanleitung.md")
SHOTS = os.path.join(ROOT, "docs", "screenshots")

HEADER = r"""
\usepackage{scrlayer-scrpage}
\pagestyle{scrheadings}
\clearpairofpagestyles
\ihead{\small\color{gray}TiLaser – Bedienungsanleitung}
\ohead{\small\color{gray}\leftmark}
\cfoot{\small\pagemark}
\automark{chapter}
\usepackage{xcolor}
\definecolor{tlred}{HTML}{D32F2F}
\definecolor{tldark}{HTML}{1C2430}
\addtokomafont{chapter}{\color{tldark}}
\addtokomafont{section}{\color{tldark}}
\addtokomafont{subsection}{\color{tldark}}
\RedeclareSectionCommand[beforeskip=0pt,afterskip=1.2\baselineskip]{chapter}
\renewcommand*{\chapterformat}{}
\usepackage{tcolorbox}
\newtcolorbox{platzhalter}{colback=gray!6,colframe=gray!45,arc=1mm,boxrule=0.6pt,halign=center,
  top=5mm,bottom=5mm}
\usepackage{etoolbox}
\AtBeginEnvironment{longtable}{\small}
\renewenvironment{quote}{\begin{tcolorbox}[colback=tlred!5,colframe=tlred!70,boxrule=0pt,leftrule=3pt,
  arc=0pt,left=2mm,right=2mm,top=1mm,bottom=1mm]}{\end{tcolorbox}}
\setlength{\emergencystretch}{3em}
\AtBeginDocument{\renewcommand{\figurename}{Abbildung}\renewcommand{\tablename}{Tabelle}}
\usepackage{chngcntr}
\counterwithout{figure}{chapter}
"""

TITLE = r"""
\begin{titlepage}
\centering
\vspace*{5cm}
{\fontsize{44}{50}\selectfont\bfseries\color{tldark} TiLaser\par}
\vspace{0.6cm}
{\LARGE Bedienungsanleitung\par}
\vspace{0.8cm}
{\color{tlred}\rule{4cm}{1.5mm}\par}
\vspace{0.8cm}
{\large Laserschneiden und -gravieren mit G-Code-Lasern\par}
\vfill
{\small\color{gray} Version VERSION \quad·\quad Stand DATUM\\[2pt] github.com/franzhefekranz/TiLaser\par}
\end{titlepage}
"""


def kpse(name: str) -> bool:
    try:
        return bool(subprocess.run(["kpsewhich", name], capture_output=True, text=True).stdout.strip())
    except FileNotFoundError:
        return False


def prepare(md_text: str) -> tuple[str, int]:
    md_text = re.sub(r"(?ms)^## Inhalt\n.*?(?=^---\n)", "", md_text)       # Inhaltsverzeichnis macht LaTeX
    md_text = re.sub(r"(?m)^# .*\n", "", md_text, count=1)                  # Titel steht auf dem Deckblatt
    md_text = re.sub(r"(?ms)^> Die Screenshots werden.*?\n\n", "", md_text)
    md_text = re.sub(r"(?m)^---\s*$", "", md_text)                          # Trennlinien zwischen Kapiteln
    md_text = md_text.replace("⏸ ", "")                                      # Zeichen fehlt in DejaVu Sans
    missing = 0

    def img(m):
        nonlocal missing
        alt, path = m.group(1), m.group(2)
        src = os.path.join(SHOTS, os.path.basename(path))
        if os.path.exists(src):
            from PIL import Image
            w, h = Image.open(src).size
            # Große Fenster auf Satzspiegelbreite, kleine Dialoge in lesbarer Größe; max. 115 mm hoch
            mm_per_px = 170 / 1440 if w >= 1000 else 0.2
            w_mm, h_mm = w * mm_per_px, h * mm_per_px
            k = min(1.0, 170 / w_mm, 115 / h_mm)
            return f"![{alt}]({src}){{width={w_mm * k:.0f}mm}}"
        missing += 1
        return f"```{{=latex}}\n\\begin{{platzhalter}}\\color{{gray}}Screenshot „{alt}“\\end{{platzhalter}}\n```"

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img, md_text), missing


def main(out_pdf: str):
    from tilaser import __version__
    if not shutil.which("pandoc") or not shutil.which("xelatex"):
        sys.exit("pandoc und xelatex werden benötigt (Linux: sudo apt install pandoc texlive-xetex texlive-fonts-recommended "
                 "texlive-latex-extra texlive-lang-german)")
    out_pdf = os.path.abspath(out_pdf)
    with open(SRC, encoding="utf-8") as f:
        md_text, missing = prepare(f.read())
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "anleitung.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write(md_text)
        hdr = os.path.join(td, "header.tex")
        with open(hdr, "w", encoding="utf-8") as f:
            f.write(HEADER)
        title = os.path.join(td, "title.tex")
        with open(title, "w", encoding="utf-8") as f:
            f.write(TITLE.replace("VERSION", __version__)
                    .replace("DATUM", datetime.date.today().strftime("%d.%m.%Y")))
        cmd = ["pandoc", src, "-f", "markdown+gfm_auto_identifiers+pipe_tables+raw_attribute",
               "-o", out_pdf, "--pdf-engine=xelatex", "--toc", "--toc-depth=2",
               "--top-level-division=chapter", "--shift-heading-level-by=-1",
               "-H", hdr, "-B", title,
               "-V", "documentclass=scrreprt", "-V", "classoption=oneside", "-V", "papersize=a4",
               "-V", "geometry=margin=20mm,top=22mm,bottom=22mm", "-V", "fontsize=10pt",
               "-V", "mainfont=DejaVu Sans", "-V", "monofont=DejaVu Sans Mono",
               "-V", "colorlinks=true", "-V", "linkcolor=tldark", "-V", "urlcolor=blue",
               "-V", "toc-title=Inhalt", "-V", "title-meta=TiLaser – Bedienungsanleitung", "-V", "author-meta=TiLaser"]
        if kpse("ngerman.ldf") or kpse("babel-german.sty"):
            cmd += ["-V", "lang=de-DE"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=td)
        if r.returncode != 0:
            sys.exit(f"pandoc/xelatex fehlgeschlagen:\n{r.stderr[-3000:]}")
    print(f"PDF erzeugt: {out_pdf}" + (f" ({missing} Screenshots als Platzhalter)" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "TiLaser-Bedienungsanleitung.pdf"))
