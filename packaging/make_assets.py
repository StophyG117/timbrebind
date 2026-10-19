"""Generate build assets: multi-size .ico and the Windows version resource.

Run from the repo root (build.ps1 does this). Outputs go to packaging/build/.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pedalclicker.app import make_icon  # noqa: E402
from pedalclicker.config import APP_NAME, AUTHOR, LICENSE_NAME, VERSION  # noqa: E402

OUT = ROOT / "packaging" / "build"
OUT.mkdir(parents=True, exist_ok=True)

sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
make_icon("idle", 256).save(OUT / "icon.ico", sizes=[(s, s) for s in sizes])

nums = [int(x) for x in VERSION.split(".")] + [0] * 4
v = tuple(nums[:4])
(OUT / "version_info.txt").write_text(f"""\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{AUTHOR}'),
      StringStruct('FileDescription', '{APP_NAME} - sustain pedal to mouse click'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'PedalClicker'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 {AUTHOR}. {LICENSE_NAME}.'),
      StringStruct('OriginalFilename', 'PedalClicker.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

(OUT / "version.txt").write_text(VERSION, encoding="ascii")
print(f"assets for {APP_NAME} {VERSION} -> {OUT}")
