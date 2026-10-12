# -*- coding: utf-8 -*-
"""
PDF 传真件批量重命名工具
--------------------------------------------------------------------------
功能：
  1. 用 OCR 识别 PDF 中「主题 / Subject」这一行之后的文字，作为「发函名」；
  2. 提取原文件名中 "_" 之后的数字作为「序列号」；
  3. 按 "序列号_发函名.pdf" 的格式重命名文件。

依赖安装：
  pip install pymupdf rapidocr-onnxruntime opencv-python numpy

使用示例：
  # 预览（不实际改名，强烈建议先跑这个）
  python rename_by_subject.py "D:\\传真件目录" --dry-run

  # 正式执行，并把原文件备份到 _backup 子目录
  python rename_by_subject.py "D:\\传真件目录"

  # 不备份，直接改名
  python rename_by_subject.py "D:\\传真件目录" --no-backup

  # 指定扫描页范围（默认只扫第 1 页；传真件标题一般在首页）
  python rename_by_subject.py "D:\\传真件目录" --pages 1

说明：
  * 输入文件既支持文本型 PDF，也支持扫描件 PDF（无文本层时自动走 OCR）。
  * OCR 会有少量错字，脚本默认只做「清理非法字符」，不做激进纠正，
    如需人工复核，重命名结果会同时输出一份 CSV 清单。
"""

import argparse
import csv
import os
import re
import shutil
import sys

from name_corrections import (
    DEFAULT_SUBJECT_KEYS,
    apply_corrections,
    load_corrections,
    load_subject_keys,
)

# ----------------------------------------------------------------------------
# 一、默认配置
# ----------------------------------------------------------------------------

# 触发「主题」的关键词（不区分大小写、允许中间有空格）
SUBJECT_KEYS = list(DEFAULT_SUBJECT_KEYS)

# 渲染分辨率。扫描件 OCR 建议 200~600 DPI。
# 实测：300 DPI 时「主题 Subject：」容易被误识别出多余字符，
# 600 DPI 下明显更干净、更稳定，所以默认取 600。
DEFAULT_DPI = 600
RENDER_DPI = DEFAULT_DPI

# OCR 识别区域：主题行在传真件版面上的大致纵向位置（占页高比例）。
# 传真件版式固定（抬头/收件人/电话/总页数/日期/主题……），
# 只裁这条横带送给 OCR，可大幅提速并避免正文干扰。
SUBJECT_BAND_TOP = 0.18      # 从页高 18% 处开始，覆盖部分传真模板较靠上的主题行
SUBJECT_BAND_BOTTOM = 0.36   # 到页高 36% 处结束

# 序列号提取规则：文件名中 "_" 之后连续的数字
SEQ_PATTERN = re.compile(r"_(?P<seq>\d+)")

# 文件名清洗：Windows 不允许的字符 + 换行/多余空格
ILLEGAL_CHARS = r'[\\/:*?"<>|\r\n\t]'
# 全角标点转半角（保留便于阅读的中文标点，仅处理会引发歧义的全角括号等）
FULLWIDTH_MAP = {
    "（": "(", "）": ")",
    "［": "[", "］": "]",
    "：": "：",   # 中文冒号保留
}

# 发函名最大长度（按字符计），防止文件名过长导致写入失败
MAX_NAME_LEN = 80


# ----------------------------------------------------------------------------
# 二、OCR 引擎
# ----------------------------------------------------------------------------

_ocr_engine = None


def get_ocr_engine():
    """惰性加载 OCR 引擎（首次调用会加载模型，耗时数秒）。"""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def render_page_image(pdf_path, page_index=0, dpi=RENDER_DPI):
    """把 PDF 的某一页渲染成 numpy 图像（RGB）。"""
    import pymupdf
    import numpy as np
    from PIL import Image

    with pymupdf.open(pdf_path) as doc:
        if page_index >= doc.page_count:
            return None
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return np.array(img)


def crop_subject_band(image, top_ratio=SUBJECT_BAND_TOP, bottom_ratio=SUBJECT_BAND_BOTTOM):
    """裁出主题行所在的横向条带，减少 OCR 干扰、提升速度。"""
    h = image.shape[0]
    y0 = max(0, int(h * top_ratio))
    y1 = min(h, int(h * bottom_ratio))
    return image[y0:y1, :]


def ocr_lines(image):
    """
    对图像做 OCR，返回按「行」聚合的结果。
    返回：[{"text": str, "y": int, "x": int}, ...]，已按 y 再按 x 排序。
    """
    ocr = get_ocr_engine()
    result, _ = ocr(image)
    if not result:
        return []

    items = []
    for it in result:
        box, text = it[0], it[1]
        text = (text or "").strip()
        if not text:
            continue
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        items.append({
            "text": text,
            "y": int(min(ys)),
            "y_center": float(sum(ys) / len(ys)),
            "x": int(min(xs)),
            "height": int(max(ys) - min(ys)),
        })

    return group_into_lines(items)


def group_into_lines(items):
    """
    把 OCR 的文本框按纵向位置聚合成「行」。
    同一行内按 x 从左到右拼接；不同行按 y 从上到下排序。
    阈值取平均字高的 0.6 倍，能容忍同一行内的轻微高低差。
    """
    if not items:
        return []

    avg_h = sum(it["height"] for it in items) / len(items) or 20
    threshold = max(8, avg_h * 0.6)

    items.sort(key=lambda it: (it["y_center"], it["x"]))
    lines = []
    for it in items:
        if lines and abs(it["y_center"] - lines[-1]["y_center"]) <= threshold:
            lines[-1]["items"].append(it)
        else:
            lines.append({"y_center": it["y_center"], "items": [it]})

    out = []
    for ln in lines:
        parts = sorted(ln["items"], key=lambda it: it["x"])
        out.append({
            "text": "".join(p["text"] for p in parts).strip(),
            "y": int(min(p["y"] for p in parts)),
            "x": int(min(p["x"] for p in parts)),
        })
    return out


# ----------------------------------------------------------------------------
# 三、从 OCR 结果里抽取「发函名」
# ----------------------------------------------------------------------------

# 正文区块的开始标记（遇到即停止，防止把 "回复 Re:" 之类吃进来）
_STOP_RE = re.compile(r"(?:回复|回覆|抄送|急件|审阅|请注释|请回复|请回收|"
                      r"re\s*[:：]|cc\s*[:：])", re.IGNORECASE)


def _subject_label_regex():
    keys = sorted(set(SUBJECT_KEYS), key=len, reverse=True)
    alternatives = "|".join(re.escape(key) for key in keys)
    return re.compile(
        r"(?:(?:主题|事由)\s*)?(?P<key>{})\s*[:：]?".format(alternatives),
        re.IGNORECASE,
    )


def _strip_label(text):
    """
    去掉文本开头的「主题 / Subject」标签，返回 (标签结束位置, 剩余正文)。
    匹配不到标签时结束位置返回 -1。
    """
    m = _subject_label_regex().search(text)
    if not m:
        return -1, text
    if m.group("key").casefold() == "关于" and m.group(0).strip() == "关于":
        return m.end(), text
    # 标签后面可能残留冒号、空格、全角空格
    rest = text[m.end():]
    rest = rest.lstrip(" 　:：")
    # 「主题」被 OCR 误识别成「头」「題」等单字时，会残留在正文开头，
    # 若残留部分很短且不是正文常见开头，则一并丢掉。
    return m.end(), rest


def _strip_residue(text):
    """清除标签被误识别后残留在正文开头的孤字（如「头」「题」）。"""
    if not text:
        return text
    # 仅当残留量极少（1~2 字）且后续马上进入正文时才清理
    for n in (1, 2):
        if len(text) > n + 4:
            head = text[:n]
            if all(ch in "头題题颗颤" for ch in head):
                tail = text[n:].lstrip(" 　:：")
                if tail.startswith(("关于", "就", "针对", "根据", "贵", "我", "#")):
                    return tail
    return text


def extract_subject_from_lines(lines, corrections=None):
    """
    从按行聚合的 OCR 结果中提取发函名。

    覆盖三种常见排版：
      A) 「主题 Subject:」单独一行，正文在下一行（同一标签行也可能带冒号）
      B) 「主题Subject：关于……的函」整行连在一起
      C) OCR 把标签和正文切成「主题」+「Subject：」两块，正文再在下一行

    判定「续行」的原则：**看这一行左边有没有同样的标签**，
    没有标签的行才可能是主题正文（比用 x 缩进判断可靠得多）。

    返回：(发函名, 命中的原始行文本列表)；未命中返回 (None, [])
    """
    if corrections is None:
        corrections = load_corrections()

    n = len(lines)
    for idx, ln in enumerate(lines):
        original_text = ln["text"]
        pos, tail = _strip_label(original_text)
        if pos >= 0:
            text = original_text
            tail = apply_corrections(tail, corrections)
        else:
            text = apply_corrections(original_text, corrections)
            pos, tail = _strip_label(text)
        if pos < 0:
            continue

        # ---------- 情况 B：标签和正文在同一行 ----------
        if len(tail) >= 4:
            return _clean_subject(tail), [text]

        # ---------- 情况 A / C：正文在后续行 ----------
        collected = []
        for j in range(idx + 1, min(idx + 4, n)):
            t = apply_corrections(lines[j]["text"], corrections).strip()
            if not t:
                continue
            # 带固定标签的行（回复 Re: / 抄送 CC: / 急件 Urgent …）说明已越界
            if _STOP_RE.search(t):
                break
            p2, _ = _strip_label(t)
            if p2 >= 0:          # 又是一个「主题/Subject」标签行 -> 真正的正文
                t = _strip_label(t)[1]
            if not t:
                continue
            collected.append(t)
            if len("".join(collected)) >= 4:
                break

        if collected:
            raw = "".join(collected)
            return _clean_subject(_strip_residue(raw)), [text] + collected

    return None, []


def extract_subject_from_pdf(pdf_path, pages=1):
    """
    主流程：PDF -> 发函名。

    策略：多路识别 + 多数表决。
      第 1 路  文本层（有文本层时又快又准）
      第 2~N 路 不同 DPI / 图像预处理下的 OCR

    为什么要表决：扫描件 OCR 难免偶发误识（例如把「主题 Subject：」多认出一个字）。
    单次识别结果不可靠，但同一页在多种参数下识别出的「共同结果」通常是对的。
    取出现次数最多的结果作为最终答案，可显著降低个别错字的影响。

    返回：(发函名, 识别来源标记, 命中行)
    """
    import pymupdf
    from collections import Counter

    candidates = []      # [(发函名, 来源, 命中行)]
    corrections = load_corrections()

    # ---------- 第 1 路：文本层 ----------
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
        for i in range(min(pages, page_count)):
            txt = doc[i].get_text().strip()
            if not txt:
                continue
            lines = [{"text": l.strip(), "y": idx, "x": 0}
                     for idx, l in enumerate(txt.splitlines()) if l.strip()]
            subject, used = extract_subject_from_lines(lines, corrections)
            if subject:
                candidates.append((subject, "text", used))

    # ---------- 第 2 路起：OCR 多参数 ----------
    for i in range(pages):
        for tag, img in _iter_ocr_variants(pdf_path, i):
            lines = ocr_lines(img)
            subject, used = extract_subject_from_lines(lines, corrections)
            if subject:
                candidates.append((subject, "ocr:" + tag, used))

    if not candidates:
        return None, "none", []

    # ---------- 多数表决 ----------
    votes = Counter(c[0] for c in candidates)
    best, best_count = votes.most_common(1)[0]

    # 若最高票并列，优先取更长的（更长的结果通常信息更完整，
    # 但要注意别把误识别的噪音选进来 —— 因此仅在票数相同时才比长度）
    tied = [s for s, c in votes.items() if c == best_count]
    if len(tied) > 1:
        best = max(tied, key=len)

    winner = next(c for c in candidates if c[0] == best)
    return best, winner[1], winner[2]


def _iter_ocr_variants(pdf_path, page_index):
    """
    生成同一页的多种 OCR 输入图像（不同 DPI / 预处理）。
    这些变体互为校验，用于后面的多数表决。
    """
    import numpy as np
    from PIL import Image, ImageOps

    img = render_page_image(pdf_path, page_index=page_index, dpi=RENDER_DPI)
    if img is None:
        return
    band = crop_subject_band(img)
    yield ("raw", band)

    # 灰度 + 自动对比度：对偏灰、偏淡的扫描件更友好
    try:
        pil = Image.fromarray(band)
        gray = ImageOps.autocontrast(pil.convert("L"))
        yield ("gray", np.array(gray.convert("RGB")))
    except Exception:
        pass

    # OTSU 二值化：对纸张发黄、底色不均的传真件更友好
    try:
        import cv2
        arr = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
        _, th = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield ("otsu", cv2.cvtColor(th, cv2.COLOR_GRAY2RGB))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 四、文件名清洗与序列号提取
# ----------------------------------------------------------------------------

def _clean_subject(subject):
    """清理 OCR 文本，使其成为合法、整洁的文件名片段。"""
    s = subject.strip()

    # 去除首尾的标签残留和分隔符
    s = re.sub(r"^(?:subject|主题|事由)\s*[:：]?\s*", "", s, flags=re.IGNORECASE)
    s = s.strip(" 　:：-—_")

    # 去掉被误并进来的尾部区块标记（如 "回复 Re:" 及其后的内容）
    cut = _STOP_RE.search(s)
    if cut:
        s = s[:cut.start()]
    s = s.strip(" 　:：-—_")

    # 全角标点转半角（按需）
    for k, v in FULLWIDTH_MAP.items():
        s = s.replace(k, v)

    # 剔除 Windows 非法字符
    s = re.sub(ILLEGAL_CHARS, "", s)

    # 合并多余空白
    if _is_mostly_cjk(s):
        s = re.sub(r"\s+", "", s)
    else:
        s = re.sub(r"\s+", " ", s).strip()

    # 截断超长名称
    if len(s) > MAX_NAME_LEN:
        s = s[:MAX_NAME_LEN]

    # 去掉结尾的句号和多余符号
    s = s.rstrip("。.、,，;；")

    return s


def _is_mostly_cjk(s):
    """判断文本是否以中文为主（中文文件名里不留空格）。"""
    if not s:
        return True
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(s) >= 0.5


def extract_sequence(filename):
    """
    提取文件名中 '_' 之后的数字作为序列号。
    例：'S55BW-9e26090415590_0211.pdf' -> '0211'

    优先取最后一个下划线后的整段数字；若该处不是数字，
    则退化为「文件名中最后一组连续数字」。
    """
    stem = os.path.splitext(os.path.basename(filename))[0]

    # 优先：取最后一个 "_" 之后的纯数字
    if "_" in stem:
        tail = stem.rsplit("_", 1)[1].strip()
        if tail.isdigit():
            return tail

    # 兜底：整个文件名中的最后一组连续数字
    nums = re.findall(r"\d+", stem)
    return nums[-1] if nums else None


# ----------------------------------------------------------------------------
# 五、主流程
# ----------------------------------------------------------------------------

def build_new_name(seq, subject, ext=".pdf"):
    """拼接新文件名：序列号_发函名.ext"""
    return "{}_{}{}".format(seq, subject, ext)


def unique_path(path):
    """若目标文件已存在，自动追加 (1)(2)… 避免覆盖。"""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists("{}({}){}".format(stem, n, ext)):
        n += 1
    return "{}({}){}".format(stem, n, ext)


def process_folder(folder, dry_run=False, backup=True, pages=1, recursive=False,
                   exts=(".pdf",), progress_callback=None):
    """批量处理目录下的 PDF，返回处理结果列表。"""
    files = []
    if recursive:
        for root, _, names in os.walk(folder):
            for n in names:
                if n.lower().endswith(exts):
                    files.append(os.path.join(root, n))
    else:
        for n in sorted(os.listdir(folder)):
            p = os.path.join(folder, n)
            if os.path.isfile(p) and n.lower().endswith(exts):
                files.append(p)

    if not files:
        print("目录下没有找到符合条件的文件：{}".format(folder))
        return []

    # 备份目录
    backup_dir = os.path.join(folder, "_backup") if backup else None
    if backup and not dry_run:
        os.makedirs(backup_dir, exist_ok=True)

    results = []
    total = len(files)
    for index, path in enumerate(files, start=1):
        name = os.path.basename(path)
        if progress_callback:
            progress_callback(index, total, name, "开始处理")
        print("-" * 72)
        print("处理：{}".format(name))

        seq = extract_sequence(name)
        if not seq:
            print("  [!] 未从文件名中提取到序列号，跳过")
            results.append({"old": name, "new": "", "seq": "",
                            "subject": "", "source": "", "status": "无序列号"})
            if progress_callback:
                progress_callback(index, total, name, "跳过：无序列号")
            continue

        subject, source, used = extract_subject_from_pdf(path, pages=pages)
        if not subject:
            print("  [!] 未识别到「主题/Subject」内容，跳过")
            results.append({"old": name, "new": "", "seq": seq,
                            "subject": "", "source": source, "status": "未识别到主题"})
            if progress_callback:
                progress_callback(index, total, name, "跳过：未识别到主题")
            continue

        new_name = build_new_name(seq, subject, os.path.splitext(name)[1])
        print("  序列号：{}".format(seq))
        print("  发函名：{}".format(subject))
        print("  识别源：{}".format("文本层" if source == "text" else "OCR"))
        print("  新名称：{}".format(new_name))

        status = "预览"
        if not dry_run:
            # 先备份原文件（保证任何时候都能回滚），再改名
            if backup:
                shutil.copy2(path, os.path.join(backup_dir, name))

            dst = unique_path(os.path.join(os.path.dirname(path), new_name))

            # ---- 安全改名 ----
            # 不用 os.rename：在 Windows 上，含半角括号/数字/字母混合的长中文文件名
            # 可能触发路径解析歧义，导致改名静默失败甚至文件丢失。
            # 这里用 shutil.move，并在移动后做存在性与大小校验，失败立即报错。
            try:
                if os.path.exists(dst):
                    raise FileExistsError("目标已存在：{}".format(dst))
                shutil.move(path, dst)
            except Exception as e:
                # 出错时尽量把文件恢复原位，避免文件"消失"
                if not os.path.exists(path) and os.path.exists(dst):
                    try:
                        shutil.move(dst, path)
                    except Exception:
                        pass
                print("  [×] 重命名失败：{}".format(e))
                results.append({"old": name, "new": new_name, "seq": seq,
                                "subject": subject, "source": source,
                                "status": "重命名失败: {}".format(e)})
                if progress_callback:
                    progress_callback(index, total, name, "失败：重命名失败")
                continue

            # 校验：源文件已不存在，目标文件存在且大小与原文件一致
            final = os.path.basename(dst)
            if os.path.exists(path):
                ok = False
            elif not os.path.exists(dst):
                ok = False
            elif backup:
                ok = os.path.getsize(dst) == os.path.getsize(
                    os.path.join(backup_dir, name))
            else:
                ok = True

            if not ok:
                print("  [!] 改名后校验未通过，请检查：{}".format(final))
                status = "已重命名(校验异常)"
            else:
                status = "已重命名" if final == new_name else "已重命名(重名自动加序号)"
            print("  结果：{}".format(final))

        results.append({"old": name, "new": new_name, "seq": seq,
                        "subject": subject, "source": source, "status": status})
        if progress_callback:
            progress_callback(index, total, name, status)

    return results


def write_report(results, out_dir):
    """把处理结果写成 CSV，方便人工复核 OCR 识别出的发函名。"""
    if not results:
        return None
    path = os.path.join(out_dir, "rename_report.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["old", "new", "seq", "subject",
                                          "source", "status"])
        w.writeheader()
        for r in results:
            w.writerow({
                "old": r["old"], "new": r["new"], "seq": r["seq"],
                "subject": r["subject"],
                "source": {"text": "文本层", "ocr": "OCR", "none": "未识别"}
                          .get(r["source"], r["source"]),
                "status": r["status"],
            })
    return path


def main():
    parser = argparse.ArgumentParser(
        description="OCR 识别 PDF 主题行，并按「序列号_发函名」批量重命名")
    parser.add_argument("folder", help="存放 PDF 的目录")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览，不实际改名（推荐先跑一次）")
    parser.add_argument("--no-backup", action="store_true",
                        help="不备份原文件（默认会在 _backup 子目录留一份副本）")
    parser.add_argument("--pages", type=int, default=1,
                        help="扫描前 N 页，默认 1 页")
    parser.add_argument("--recursive", action="store_true",
                        help="递归处理子目录")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                        help="OCR 渲染分辨率，默认 600")
    args = parser.parse_args()

    global RENDER_DPI
    RENDER_DPI = args.dpi
    global SUBJECT_KEYS
    SUBJECT_KEYS = load_subject_keys()

    if not os.path.isdir(args.folder):
        print("目录不存在：{}".format(args.folder))
        sys.exit(1)

    print("=" * 72)
    print("模式：{}".format("预览（不修改文件）" if args.dry_run else "正式重命名"))
    print("目录：{}".format(args.folder))
    print("=" * 72)

    results = process_folder(
        args.folder,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        pages=args.pages,
        recursive=args.recursive,
    )

    ok = [r for r in results if r["status"] not in ("无序列号", "未识别到主题")]
    bad = [r for r in results if r["status"] in ("无序列号", "未识别到主题")]

    print("=" * 72)
    print("合计 {} 个文件：成功 {}，失败 {}，预览 {}"
          .format(len(results), len(ok), len(bad),
                  sum(1 for r in results if r["status"] == "预览")))

    report = write_report(results, args.folder)
    if report:
        print("处理清单：{}".format(report))
    if args.dry_run:
        print("提示：以上为预览结果，确认无误后去掉 --dry-run 重新执行即可。")


if __name__ == "__main__":
    main()
