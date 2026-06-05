import os
import json
import pandas as pd
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 上次选择目录的记忆文件（JSON）所在文件夹
LAST_DIR_SAVE_DIR = Path(r"C:\My files\Programs_codes")
LAST_DIR_JSON = LAST_DIR_SAVE_DIR / "Manifest_index_last_path.json"

def get_last_scan_root() -> str | None:
    """从 JSON 读取上次扫描根目录；无效或不存在则返回 None。"""
    if not LAST_DIR_JSON.is_file():
        return None
    try:
        with open(LAST_DIR_JSON, encoding="utf-8") as f:
            data = json.load(f)
        last = data.get("last_scan_root")
        if last and Path(last).is_dir():
            return last
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None

def _save_last_scan_root(directory: str):
    """将本次选择的扫描根目录写入 JSON。"""
    try:
        LAST_DIR_SAVE_DIR.mkdir(parents=True, exist_ok=True)
        with open(LAST_DIR_JSON, "w", encoding="utf-8") as f:
            json.dump({"last_scan_root": directory}, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"无法保存上次目录到配置文件: {e}")

# 配置信息
MANIFEST_NAME = "plot_manifest.xlsx"
MANIFEST_COLUMNS = ["File_Path", "Rel_Path", "Label"]
# 数据文件后缀（仅 csv、txt；不统计 xlsx）
DATA_EXTS = {'.csv', '.txt'}
# 排除的文件夹名称
EXCLUDED_DIRS = {'__pycache__', '.git', '.vscode', '.idea'}


def pick_scan_root_directory() -> Path | None:
    """弹出文件夹选择对话框，返回数据根目录或 None（用户取消）。"""
    root = tk.Tk()
    root.withdraw()
    initial = get_last_scan_root() or os.getcwd()
    if not Path(initial).is_dir():
        initial = os.getcwd()
    selected_dir = filedialog.askdirectory(
        title="请选择要扫描的数据根目录",
        initialdir=initial,
    )
    root.destroy()
    if not selected_dir:
        return None
    _save_last_scan_root(selected_dir)
    return Path(selected_dir)


def build_manifest_dataframe(root_dir: Path) -> tuple[pd.DataFrame, int] | None:
    """
    扫描 root_dir 下 csv/txt，构建清单 DataFrame。
    返回 (df, 文件条数)；无有效文件时返回 None。
    """
    data_list = []
    for path in root_dir.rglob('*'):
        if path.is_dir():
            continue
        if any(part.startswith('.') or part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in DATA_EXTS:
            if path.name == MANIFEST_NAME:
                continue
            data_list.append({
                'File_Path': str(path.absolute()),
                'Rel_Path': path.relative_to(root_dir).as_posix(),
                'Label': path.stem,
            })

    if not data_list:
        return None

    df = pd.DataFrame(data_list)
    df = df.sort_values(by='File_Path').reset_index(drop=True)

    empty = {c: None for c in MANIFEST_COLUMNS}
    rows = []
    prev_parent = None
    for _, row in df.iterrows():
        parent = Path(row['File_Path']).parent
        if prev_parent is not None and parent != prev_parent:
            rows.append(empty.copy())
        rows.append(row.to_dict())
        prev_parent = parent
    df = pd.DataFrame(rows).reindex(columns=MANIFEST_COLUMNS)
    return df, len(data_list)


def write_manifest(df: pd.DataFrame, root_dir: Path) -> Path:
    """将清单写入 root_dir / MANIFEST_NAME，返回输出路径。"""
    output_file = root_dir / MANIFEST_NAME
    df.to_excel(output_file, index=False)
    return output_file


def generate_manifest_at(root_dir: Path) -> Path | None:
    """在指定根目录扫描并生成清单，成功返回 manifest 路径。"""
    print(f"正在扫描目录: {root_dir}")
    built = build_manifest_dataframe(root_dir)
    if built is None:
        print("未发现有效的数据文件。")
        return None
    df, file_count = built
    try:
        output_file = write_manifest(df, root_dir)
    except Exception as e:
        print(f"保存 Excel 失败: {e}")
        return None
    print("-" * 30)
    print(f"成功在母文件夹生成总清单: {output_file}")
    print(f"共索引文件数: {file_count}")
    print("-" * 30)
    return output_file


def generate_manifest_interactive() -> Path | None:
    """选择文件夹、生成清单，成功返回 manifest 路径。"""
    root_dir = pick_scan_root_directory()
    if root_dir is None:
        print("用户取消了选择。")
        return None
    return generate_manifest_at(root_dir)


def generate_manifests():
    """独立运行入口：交互式生成清单。"""
    generate_manifest_interactive()


if __name__ == "__main__":
    # 确保安装了 pandas 和 openpyxl: pip install pandas openpyxl
    generate_manifests()
