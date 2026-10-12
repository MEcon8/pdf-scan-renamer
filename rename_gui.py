# -*- coding: utf-8 -*-
"""GUI for both PDF renamers and their user-editable OCR settings."""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import rename_by_largest_font
import rename_by_subject
from name_corrections import (
    DEFAULT_CORRECTIONS,
    DEFAULT_SUBJECT_KEYS,
    load_corrections,
    load_subject_keys,
    save_corrections,
    save_subject_keys,
)

AUTHOR = "005542"


class RenameApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF 批量重命名工具")
        self.geometry("900x660")
        self.minsize(760, 560)

        self.subject_folder_var = tk.StringVar()
        self.largest_folder_var = tk.StringVar()
        self.pages_var = tk.IntVar(value=1)
        self.dpi_var = tk.IntVar(value=600)
        self.dry_run_var = tk.BooleanVar(value=True)
        self.backup_var = tk.BooleanVar(value=True)
        self.recursive_var = tk.BooleanVar(value=False)
        self.queue = queue.Queue()
        self.worker = None
        self.settings_load_error = None

        try:
            self.corrections = load_corrections()
        except (OSError, ValueError) as exc:
            self.corrections = dict(DEFAULT_CORRECTIONS)
            self.settings_load_error = "OCR 纠错词典加载失败：{}".format(exc)
        try:
            self.subject_keys = load_subject_keys()
        except (OSError, ValueError) as exc:
            self.subject_keys = list(DEFAULT_SUBJECT_KEYS)
            error = "SUBJECT_KEYS 加载失败：{}".format(exc)
            self.settings_load_error = "\n".join(
                filter(None, (self.settings_load_error, error))
            )
        rename_by_subject.SUBJECT_KEYS = list(self.subject_keys)

        self._build_widgets()
        self._load_settings_fields()
        self.after(100, self._poll_events)
        if self.settings_load_error:
            self.after(100, lambda: messagebox.showerror("配置加载失败", self.settings_load_error))

    def _build_widgets(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        title_row = ttk.Frame(root)
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        title_row.columnconfigure(0, weight=1)
        ttk.Label(
            title_row,
            text="PDF 批量重命名工具",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(title_row, text="作者：{}".format(AUTHOR)).grid(row=0, column=1, sticky="e")

        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        self.rename_tab = ttk.Frame(self.tabs, padding=12)
        self.settings_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.rename_tab, text="文件处理")
        self.tabs.add(self.settings_tab, text="识别设置")
        self._build_rename_tab()
        self._build_settings_tab()

        self.current_var = tk.StringVar(value="等待开始")
        self.detail_var = tk.StringVar(value="请选择要处理的 PDF 文件夹")
        status = ttk.LabelFrame(root, text="处理状态", padding=10)
        status.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        status.columnconfigure(1, weight=1)
        ttk.Label(status, text="当前文件").grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.current_var).grid(row=0, column=1, sticky="w", padx=8)
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(status, textvariable=self.detail_var, wraplength=800).grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        self.status_text = tk.Text(status, height=7, state="disabled", wrap="word")
        self.status_text.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_rename_tab(self):
        tab = self.rename_tab
        tab.columnconfigure(1, weight=1)
        ttk.Label(
            tab,
            text="按传真主题提取发函名，使用「序列号_发函名」格式重命名",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(tab, text="主题行程序输入文件夹").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(tab, textvariable=self.subject_folder_var).grid(
            row=1, column=1, sticky="ew", padx=8, pady=5
        )
        self.subject_browse_button = ttk.Button(
            tab,
            text="浏览...",
            command=lambda: self._choose_folder(
                self.subject_folder_var, "选择主题行程序 PDF 文件夹"
            ),
        )
        self.subject_browse_button.grid(row=1, column=2, pady=5)
        self.subject_start_button = ttk.Button(
            tab, text="运行主题行重命名", command=lambda: self._start("subject")
        )
        self.subject_start_button.grid(row=2, column=1, sticky="e", pady=(0, 14))

        ttk.Separator(tab).grid(row=3, column=0, columnspan=3, sticky="ew", pady=5)
        ttk.Label(
            tab,
            text="按首页上半部分最大字号标题重命名",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(tab, text="最大字号程序输入文件夹").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(tab, textvariable=self.largest_folder_var).grid(
            row=5, column=1, sticky="ew", padx=8, pady=5
        )
        self.largest_browse_button = ttk.Button(
            tab,
            text="浏览...",
            command=lambda: self._choose_folder(
                self.largest_folder_var, "选择最大字号程序 PDF 文件夹"
            ),
        )
        self.largest_browse_button.grid(row=5, column=2, pady=5)
        self.largest_start_button = ttk.Button(
            tab, text="运行最大字号重命名", command=lambda: self._start("largest")
        )
        self.largest_start_button.grid(row=6, column=1, sticky="e", pady=(0, 14))

        options = ttk.LabelFrame(tab, text="共用处理选项", padding=10)
        options.grid(row=7, column=0, columnspan=3, sticky="ew", pady=5)
        ttk.Checkbutton(options, text="预览模式（不改名）", variable=self.dry_run_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Checkbutton(options, text="备份原文件", variable=self.backup_var).grid(
            row=0, column=1, sticky="w", padx=16
        )
        ttk.Checkbutton(options, text="递归处理子目录", variable=self.recursive_var).grid(
            row=0, column=2, sticky="w"
        )
        ttk.Label(options, text="主题程序页数").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(options, from_=1, to=99, width=6, textvariable=self.pages_var).grid(
            row=1, column=1, sticky="w", padx=16, pady=(8, 0)
        )
        ttk.Label(options, text="OCR DPI").grid(row=1, column=2, sticky="e", pady=(8, 0))
        ttk.Spinbox(
            options, from_=100, to=1200, increment=50, width=8, textvariable=self.dpi_var
        ).grid(row=1, column=3, sticky="w", padx=8, pady=(8, 0))

    def _build_settings_tab(self):
        tab = self.settings_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)

        ttk.Label(
            tab,
            text="OCR 纠错词典（JSON：错误文本作为键，替换文本作为值；空字符串表示删除）",
        ).grid(row=0, column=0, sticky="w")
        correction_frame = ttk.Frame(tab)
        correction_frame.grid(row=1, column=0, sticky="nsew", pady=(4, 10))
        correction_frame.rowconfigure(0, weight=1)
        correction_frame.columnconfigure(0, weight=1)
        self.corrections_text = tk.Text(correction_frame, height=12, wrap="none")
        self.corrections_text.grid(row=0, column=0, sticky="nsew")
        correction_scroll = ttk.Scrollbar(
            correction_frame, orient="vertical", command=self.corrections_text.yview
        )
        correction_scroll.grid(row=0, column=1, sticky="ns")
        self.corrections_text.configure(yscrollcommand=correction_scroll.set)

        ttk.Label(
            tab,
            text="SUBJECT_KEYS（每行一个主题关键词；用于识别主题标签，可自行添加）",
        ).grid(row=2, column=0, sticky="w")
        keys_frame = ttk.Frame(tab)
        keys_frame.grid(row=3, column=0, sticky="nsew", pady=(4, 10))
        keys_frame.rowconfigure(0, weight=1)
        keys_frame.columnconfigure(0, weight=1)
        self.subject_keys_text = tk.Text(keys_frame, height=7, wrap="none")
        self.subject_keys_text.grid(row=0, column=0, sticky="nsew")
        keys_scroll = ttk.Scrollbar(
            keys_frame, orient="vertical", command=self.subject_keys_text.yview
        )
        keys_scroll.grid(row=0, column=1, sticky="ns")
        self.subject_keys_text.configure(yscrollcommand=keys_scroll.set)

        ttk.Button(tab, text="保存识别设置", command=self._save_settings).grid(
            row=4, column=0, sticky="e"
        )

    def _load_settings_fields(self):
        self.corrections_text.delete("1.0", "end")
        self.corrections_text.insert(
            "1.0", json.dumps(self.corrections, ensure_ascii=False, indent=2)
        )
        self.subject_keys_text.delete("1.0", "end")
        self.subject_keys_text.insert("1.0", "\n".join(self.subject_keys))

    def _save_settings(self):
        try:
            corrections = json.loads(self.corrections_text.get("1.0", "end"))
            if not isinstance(corrections, dict):
                raise ValueError("OCR 纠错词典必须是 JSON 对象")
            keys = [
                line.strip()
                for line in self.subject_keys_text.get("1.0", "end").splitlines()
                if line.strip()
            ]
            save_corrections(corrections)
            save_subject_keys(keys)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            messagebox.showerror("设置无效", str(exc))
            return

        self.corrections = corrections
        self.subject_keys = keys
        rename_by_subject.SUBJECT_KEYS = list(keys)
        self._append_log("已保存 OCR 纠错词典和 SUBJECT_KEYS。")
        messagebox.showinfo("保存成功", "识别设置已保存，后续运行将使用新设置。")

    def _choose_folder(self, variable, title):
        folder = filedialog.askdirectory(title=title)
        if folder:
            variable.set(folder)

    def _append_log(self, text):
        self.status_text.configure(state="normal")
        self.status_text.insert("end", text + "\n")
        self.status_text.see("end")
        self.status_text.configure(state="disabled")

    def _start(self, mode):
        variable = self.subject_folder_var if mode == "subject" else self.largest_folder_var
        folder = variable.get().strip()
        if not folder:
            messagebox.showwarning("缺少文件夹", "请先选择对应程序的输入文件夹。")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("文件夹不存在", folder)
            return
        try:
            pages = max(1, int(self.pages_var.get()))
            dpi = max(100, min(1200, int(self.dpi_var.get())))
        except (TypeError, ValueError):
            messagebox.showerror("参数错误", "页数和 OCR DPI 必须是有效数字。")
            return

        self._set_running(True)
        mode_name = "主题行" if mode == "subject" else "最大字号"
        self.current_var.set("正在扫描文件...")
        self.detail_var.set("首次运行可能需要加载 OCR 模型，请稍候")
        self._append_log("开始{}处理：{}".format(mode_name, folder))
        self.worker = threading.Thread(
            target=self._run,
            args=(
                mode,
                folder,
                pages,
                dpi,
                self.dry_run_var.get(),
                self.backup_var.get(),
                self.recursive_var.get(),
            ),
            daemon=True,
        )
        self.worker.start()

    def _set_running(self, running):
        state = "disabled" if running else "normal"
        for button in (
            self.subject_browse_button,
            self.largest_browse_button,
            self.subject_start_button,
            self.largest_start_button,
        ):
            button.configure(state=state)
        self.tabs.tab(self.settings_tab, state=state)
        if running:
            self.progress.configure(value=0, maximum=1)

    def _run(self, mode, folder, pages, dpi, dry_run, backup, recursive):
        try:
            def on_progress(index, total, name, status):
                self.queue.put(("progress", index, total, name, status))

            if mode == "subject":
                rename_by_subject.RENDER_DPI = dpi
                rename_by_subject.SUBJECT_KEYS = load_subject_keys()
                results = rename_by_subject.process_folder(
                    folder,
                    dry_run=dry_run,
                    backup=backup,
                    pages=pages,
                    recursive=recursive,
                    progress_callback=on_progress,
                )
                report = rename_by_subject.write_report(results, folder)
            else:
                results = rename_by_largest_font.process_folder(
                    folder,
                    dry_run=dry_run,
                    backup=backup,
                    recursive=recursive,
                    dpi=dpi,
                    progress_callback=on_progress,
                )
                report = rename_by_largest_font.write_report(results, folder)
            self.queue.put(("done", mode, results, report))
        except Exception as exc:
            self.queue.put(("error", mode, "{}: {}".format(type(exc).__name__, exc)))

    def _poll_events(self):
        try:
            while True:
                event = self.queue.get_nowait()
                if event[0] == "progress":
                    _, index, total, name, status = event
                    self.progress.configure(maximum=max(1, total), value=index)
                    self.current_var.set("{}/{}：{}".format(index, total, name))
                    self.detail_var.set(status)
                    self._append_log("[{} / {}] {}：{}".format(index, total, status, name))
                elif event[0] == "done":
                    _, mode, results, report = event
                    self._set_running(False)
                    skipped_statuses = (
                        ("无序列号", "未识别到主题")
                        if mode == "subject"
                        else ("未识别到有效文字",)
                    )
                    skipped = sum(
                        1 for item in results if item["status"] in skipped_statuses
                    )
                    failed = sum(1 for item in results if "失败" in item["status"])
                    success = len(results) - skipped - failed
                    self.current_var.set("处理完成")
                    summary = "成功 {}，跳过 {}，失败 {}，报告：{}".format(
                        success, skipped, failed, report or "无"
                    )
                    self.detail_var.set(summary)
                    self._append_log("处理完成，共 {} 个文件。{}".format(len(results), summary))
                    messagebox.showinfo("处理完成", "共处理 {} 个文件。\n{}".format(
                        len(results), summary
                    ))
                elif event[0] == "error":
                    _, _, error = event
                    self._set_running(False)
                    self.current_var.set("处理失败")
                    self.detail_var.set(error)
                    self._append_log("错误：" + error)
                    messagebox.showerror("处理失败", error)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


if __name__ == "__main__":
    RenameApp().mainloop()
