# -*- coding: utf-8 -*-
"""
按 PDF 首页最大字号文字批量重命名。

依赖安装：
  pip install -r requirements.txt

用法：
  python rename_by_largest_font.py "/mnt/d/扫描文件" --dry-run
  python rename_by_largest_font.py "D:\\扫描文件" --dry-run  # WSL 下自动转换
  python rename_by_largest_font.py "/mnt/d/扫描文件" --recursive --no-backup

脚本根据 OCR 文本框高度估算字号；正式执行前建议先用 --dry-run 检查结果。
"""

import argparse
import csv
import os
import platform
import re
import shutil
import subprocess
import sys
from statistics import median

import numpy as np

from name_corrections import apply_corrections, load_corrections
from rename_by_subject import get_ocr_engine, render_page_image


DEFAULT_DPI = 400
MAX_NAME_LEN = 100
MAX_TITLE_LINES = 3
BLACK_PIXEL_MAX_CHANNEL_SPREAD = 25
BLACK_PIXEL_MAX_CHANNEL_VALUE = 200
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")
ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_folder_path(folder):
    """将 Windows 盘符路径转换为 WSL 路径，并标准化其他本地路径。"""
    folder = os.path.expanduser(folder)
    if WINDOWS_ABSOLUTE_PATH.match(folder) and sys.platform.startswith("linux"):
        is_wsl = (
            "WSL_DISTRO_NAME" in os.environ
            or "WSL_INTEROP" in os.environ
            or "microsoft" in platform.release().lower()
        )
        if not is_wsl:
            raise ValueError(
                "当前 Linux 环境不能直接使用 Windows 盘符路径；"
                "请传入 Linux 路径，例如 /mnt/d/扫描文件"
            )

        wslpath = shutil.which("wslpath")
        if not wslpath:
            raise ValueError("找不到 wslpath，请在 WSL 中安装/启用该命令")
        converted = subprocess.run(
            [wslpath, "-u", folder],
            check=False,
            capture_output=True,
            text=True,
        )
        if converted.returncode != 0:
            message = converted.stderr.strip() or "wslpath 转换失败"
            raise ValueError(message)
        folder = converted.stdout.strip()
        if not folder:
            raise ValueError("wslpath 未返回有效路径")

    return os.path.abspath(folder)


def _box_metrics(box):
    """返回 OCR 四边形的 (x, y, 宽, 高)，无法解析时返回 None。"""
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def keep_black_text_pixels(image):
    """保留近黑色像素，将彩色或过亮像素置白，过滤彩色印章。"""
    channels = image.astype(np.int16)
    channel_spread = channels.max(axis=2) - channels.min(axis=2)
    is_black = (
        (channel_spread <= BLACK_PIXEL_MAX_CHANNEL_SPREAD)
        & (channels.max(axis=2) <= BLACK_PIXEL_MAX_CHANNEL_VALUE)
    )
    grayscale = channels.mean(axis=2).astype(image.dtype)
    grayscale_rgb = np.repeat(grayscale[:, :, np.newaxis], 3, axis=2)
    return np.where(is_black[:, :, np.newaxis], grayscale_rgb, 255)


def group_ocr_lines(items):
    """按 OCR 框的位置将同一基线上的文字合并为行。"""
    if not items:
        return []

    line_gap = max(10, median(item["height"] for item in items) * 0.55)
    lines = []
    for item in sorted(items, key=lambda value: (value["y_center"], value["x"])):
        matches = [
            line for line in lines
            if abs(item["y_center"] - line["y_center"]) <= max(
                line_gap, max(item["height"], line["height"]) * 0.35
            )
        ]
        if matches:
            line = min(matches, key=lambda value: abs(item["y_center"] - value["y_center"]))
            line["items"].append(item)
            line["y_center"] = median(part["y_center"] for part in line["items"])
            line["height"] = median(part["height"] for part in line["items"])
        else:
            lines.append({
                "y_center": item["y_center"],
                "height": item["height"],
                "items": [item],
            })

    result = []
    for line in lines:
        parts = sorted(line["items"], key=lambda value: value["x"])
        result.append({
            "text": "".join(part["text"] for part in parts).strip(),
            "height": median(part["height"] for part in parts),
            "y": min(part["y"] for part in parts),
        })
    return [line for line in result if line["text"]]


def extract_largest_text_from_pdf(pdf_path, dpi=DEFAULT_DPI):
    """OCR PDF 首页上半部分，返回字号估算值最大的标题及其文本框高度。"""
    image = render_page_image(pdf_path, page_index=0, dpi=dpi)
    if image is None:
        return None, 0

    image = image[:image.shape[0] // 2]
    image = keep_black_text_pixels(image)
    result, _ = get_ocr_engine()(image)
    corrections = load_corrections()
    items = []
    for entry in result or []:
        if len(entry) < 2:
            continue
        metrics = _box_metrics(entry[0])
        text = apply_corrections((entry[1] or "").strip(), corrections)
        if metrics is None or not text:
            continue
        x, y, _, height = metrics
        if height <= 0:
            continue
        items.append({
            "text": text,
            "x": x,
            "y": y,
            "y_center": y + height / 2,
            "height": height,
        })

    lines = group_ocr_lines(items)
    if not lines:
        return None, 0

    # 字号相同或接近时，优先选择文字较长的行，避免只取到单个大字。
    largest = max(line["height"] for line in lines)
    candidates = [
        line for line in lines
        if line["height"] >= largest * 0.97
    ]
    anchor = max(candidates, key=lambda line: (len(line["text"]), -line["y"]))
    anchor_index = next(index for index, line in enumerate(lines) if line is anchor)
    title_lines = [anchor]
    previous = anchor
    for line in lines[anchor_index + 1:anchor_index + MAX_TITLE_LINES]:
        if line["height"] < largest * 0.75:
            break
        vertical_gap = line["y"] - (previous["y"] + previous["height"])
        if vertical_gap > max(line["height"], previous["height"]) * 1.25:
            break
        title_lines.append(line)
        previous = line

    title = title_lines[0]["text"]
    for line in title_lines[1:]:
        previous_char = title[-1]
        next_char = line["text"][0]
        if previous_char.isascii() and previous_char.isalnum() and next_char.isascii() and next_char.isalnum():
            title += " "
        title += line["text"]
    return title, anchor["height"]


def clean_filename(text):
    """清理 OCR 文本，使其可作为 Windows 文件名。"""
    name = ILLEGAL_CHARS.sub("", text).strip().rstrip(". ")
    name = re.sub(r"\s+", " ", name)
    if not name:
        return ""
    name = name[:MAX_NAME_LEN].rstrip(". ")
    if name.upper() in RESERVED_NAMES:
        name = "_" + name
    return name


def unique_path(path):
    """目标已存在时追加序号，避免覆盖文件。"""
    if not os.path.exists(path):
        return path
    stem, extension = os.path.splitext(path)
    index = 1
    while os.path.exists("{} ({}){}".format(stem, index, extension)):
        index += 1
    return "{} ({}){}".format(stem, index, extension)


def collect_pdfs(folder, recursive=False):
    """收集待处理 PDF；递归时跳过备份目录。"""
    files = []
    if recursive:
        for root, dirs, names in os.walk(folder):
            dirs[:] = [name for name in dirs if name.lower() != "_backup_largest_font"]
            files.extend(
                os.path.join(root, name)
                for name in names if name.lower().endswith(".pdf")
            )
    else:
        files.extend(
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if name.lower().endswith(".pdf")
            and os.path.isfile(os.path.join(folder, name))
        )
    return files


def process_folder(
    folder,
    dry_run=False,
    backup=True,
    recursive=False,
    dpi=DEFAULT_DPI,
    progress_callback=None,
):
    """处理目录中的 PDF，返回可写入 CSV 的结果。"""
    files = collect_pdfs(folder, recursive=recursive)
    if not files:
        print("目录下没有找到 PDF 文件：{}".format(folder))
        return []

    backup_root = os.path.join(folder, "_backup_largest_font")
    results = []
    for index, path in enumerate(files, start=1):
        old_name = os.path.basename(path)
        extension = os.path.splitext(old_name)[1]
        if progress_callback:
            progress_callback(index, len(files), old_name, "开始处理")
        print("-" * 72)
        print("[{}/{}] 处理：{}".format(index, len(files), path))

        try:
            text, font_height = extract_largest_text_from_pdf(path, dpi=dpi)
            new_stem = clean_filename(text or "")
        except Exception as exc:
            status = "处理失败：{}: {}".format(type(exc).__name__, exc)
            print("  [×] {}".format(status))
            results.append({
                "old": path, "new": "", "text": "", "font_height": "",
                "status": status,
            })
            if progress_callback:
                progress_callback(index, len(files), old_name, status)
            continue

        if not new_stem:
            print("  [!] 首页未识别到可用文字，跳过")
            results.append({
                "old": path, "new": "", "text": text or "",
                "font_height": font_height, "status": "未识别到有效文字",
            })
            if progress_callback:
                progress_callback(index, len(files), old_name, "未识别到有效文字")
            continue

        target_name = new_stem + extension
        target_path = os.path.join(os.path.dirname(path), target_name)
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(target_path)):
            status = "名称未变化"
        else:
            if os.path.exists(target_path):
                target_path = unique_path(target_path)
                target_name = os.path.basename(target_path)
            status = "预览" if dry_run else "已重命名"

            if not dry_run:
                try:
                    if backup:
                        relative_path = os.path.relpath(path, folder)
                        backup_path = os.path.join(backup_root, relative_path)
                        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                        backup_path = unique_path(backup_path)
                        shutil.copy2(path, backup_path)

                    original_size = os.path.getsize(path)
                    shutil.move(path, target_path)
                    if not os.path.isfile(target_path) or os.path.getsize(target_path) != original_size:
                        raise OSError("重命名后文件存在性或大小校验失败")
                except Exception as exc:
                    if not os.path.exists(path) and os.path.exists(target_path):
                        try:
                            shutil.move(target_path, path)
                        except OSError as restore_error:
                            print("  [×] 恢复原文件失败：{}".format(restore_error))
                    status = "重命名失败：{}: {}".format(type(exc).__name__, exc)

        print("  最大文字：{}".format(text))
        print("  文本框高度：{:.1f}px".format(font_height))
        print("  新名称：{}".format(target_name if status != "名称未变化" else old_name))
        print("  结果：{}".format(status))
        results.append({
            "old": path, "new": target_name, "text": text,
            "font_height": "{:.1f}".format(font_height), "status": status,
        })
        if progress_callback:
            progress_callback(index, len(files), old_name, status)
    return results


def write_report(results, folder):
    """输出 OCR 结果及处理状态，便于检查识别错误。"""
    if not results:
        return None
    report_path = os.path.join(folder, "rename_by_largest_font_report.csv")
    with open(report_path, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(
            output, fieldnames=["old", "new", "text", "font_height", "status"]
        )
        writer.writeheader()
        writer.writerows(results)
    return report_path


def main():
    parser = argparse.ArgumentParser(
        description="OCR 识别 PDF 首页字号最大的文字，并据此批量重命名"
    )
    parser.add_argument("folder", help="存放 PDF 文件的目录")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际改名")
    parser.add_argument("--no-backup", action="store_true", help="不备份原文件")
    parser.add_argument("--recursive", action="store_true", help="递归处理子目录")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="首页 OCR 分辨率，默认 400")
    args = parser.parse_args()

    try:
        folder = normalize_folder_path(args.folder)
    except (OSError, ValueError) as exc:
        parser.error("无法解析输入目录：{}".format(exc))

    if not os.path.isdir(folder):
        print("目录不存在：{}".format(folder))
        return 1
    if args.dpi < 100 or args.dpi > 1200:
        print("DPI 必须在 100 到 1200 之间")
        return 1

    print("=" * 72)
    print("模式：{}".format("预览（不修改文件）" if args.dry_run else "正式重命名"))
    print("目录：{}".format(folder))
    print("备份：{}".format("关闭" if args.no_backup else "_backup_largest_font"))
    print("=" * 72)

    results = process_folder(
        folder,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        recursive=args.recursive,
        dpi=args.dpi,
    )
    report = write_report(results, folder)
    failed = sum(
        1 for result in results
        if result["status"].startswith(("处理失败", "重命名失败"))
        or result["status"] == "未识别到有效文字"
    )
    print("=" * 72)
    print("处理 {} 个文件，失败或跳过 {} 个".format(len(results), failed))
    if report:
        print("处理清单：{}".format(report))
    if args.dry_run:
        print("确认预览结果无误后，去掉 --dry-run 即可正式重命名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
