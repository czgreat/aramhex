"""EXE 的版本信息（右键属性 → 详细信息）。从 lolhex/__init__.py 取版本号，不另存一份。"""
import ast
import re
from pathlib import Path


def app_version(root: Path) -> str:
    tree = ast.parse((Path(root) / "lolhex" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            version = ast.literal_eval(node.value)
            if re.fullmatch(r"\d{1,5}(\.\d{1,5}){1,3}", version):
                return version
    raise ValueError("lolhex/__init__.py 里没有合法的 __version__")


def version_resource(root: Path):
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

    version = app_version(root)
    numbers = tuple(int(p) for p in version.split("."))
    numbers += (0,) * (4 - len(numbers))
    text = {"FileDescription": "AramHex 海克斯大乱斗助手", "FileVersion": version, "InternalName": "AramHex",
            "OriginalFilename": "AramHex.exe", "ProductName": "AramHex", "ProductVersion": version}
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers, mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable("080404B0", [StringStruct(k, v) for k, v in text.items()])]),
              VarFileInfo([VarStruct("Translation", [0x0804, 1200])])])
