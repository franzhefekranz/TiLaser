"""Lizenztexte der gebündelten Bibliotheken in den Programmordner kopieren.

Aufruf nach PyInstaller:  python tools/collect_licenses.py dist/TiLaser
Legt <ziel>/licenses/<paket>/… sowie <ziel>/THIRD_PARTY_LICENSES.txt und LICENSE.txt an.
Die Texte stammen aus den installierten Paketen (dist-info), sind also genau die der gebündelten Fassung.
"""
from __future__ import annotations

import os
import shutil
import sys
from importlib import metadata

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGES = ["PyQt6", "PyQt6-Qt6", "PyQt6-sip", "numpy", "pillow", "fonttools", "pyserial", "pyclipper"]
NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "LGPL", "GPL")


def _is_license(rel: str) -> bool:
    base = os.path.basename(rel).upper()
    parts = rel.replace("\\", "/").split("/")
    in_dist_info = any(p.endswith(".dist-info") for p in parts)
    return (in_dist_info and ("licenses" in parts or base.startswith(NAMES))) or \
        (base.startswith(NAMES) and len(parts) <= 2)


def collect(target: str) -> list[str]:
    out_dir = os.path.join(target, "licenses")
    os.makedirs(out_dir, exist_ok=True)
    done = []
    for name in PACKAGES:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"  (nicht installiert: {name})")
            continue
        dest = os.path.join(out_dir, f"{dist.metadata['Name']}-{dist.version}")
        n = 0
        for f in dist.files or []:
            rel = str(f)
            if not _is_license(rel):
                continue
            src = f.locate()
            if os.path.isfile(src):
                parts = rel.replace("\\", "/").split("/")
                sub = parts[parts.index("licenses") + 1:] if "licenses" in parts else parts[-1:]
                out = os.path.join(dest, *sub)               # Unterordner behalten (gleiche Dateinamen)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(src, out)
                n += 1
        lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if n == 0:                                    # mindestens die Angabe aus den Metadaten sichern
            os.makedirs(dest, exist_ok=True)
            with open(os.path.join(dest, "LICENSE-Metadaten.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"{dist.metadata['Name']} {dist.version}\nLizenz: {lic.splitlines()[0] if lic else '?'}\n"
                         f"Projekt: {dist.metadata.get('Home-page') or ''}\n")
        done.append(f"{dist.metadata['Name']} {dist.version}: {n} Datei(en)")
    # Python selbst (Laufzeit wird mitgeliefert)
    for cand in (os.path.join(sys.base_prefix, "LICENSE.txt"),
                 os.path.join(sys.base_prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                              "LICENSE.txt"),
                 f"/usr/share/doc/python{sys.version_info.major}.{sys.version_info.minor}/copyright"):
        if os.path.isfile(cand):
            d = os.path.join(out_dir, f"Python-{sys.version.split()[0]}")
            os.makedirs(d, exist_ok=True)
            shutil.copy2(cand, os.path.join(d, "LICENSE.txt"))
            done.append(f"Python {sys.version.split()[0]}")
            break
    shutil.copy2(os.path.join(ROOT, "tilaser", "data", "THIRD_PARTY_LICENSES.txt"),
                 os.path.join(target, "THIRD_PARTY_LICENSES.txt"))
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(target, "LICENSE.txt"))
    return done


if __name__ == "__main__":
    tgt = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "TiLaser")
    for line in collect(tgt):
        print("  " + line)
    print(f"Lizenzen gesammelt in {os.path.join(tgt, 'licenses')}")
