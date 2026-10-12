# PDF 批量重命名工具

本程序包含两种 PDF 重命名方式，可在同一个图形界面中分别选择文件夹和运行。程序主要面向 Windows；源码也可在 Linux/WSL 的 Python 环境中运行。

## 功能

### 1. 按传真主题重命名

- 从 PDF 文本层或 OCR 中识别“主题 / Subject”等主题标签及其函件主题。
- 扫描件 OCR 默认只识别页面高度约 **18%–36%** 的区域；有文本层的 PDF 会在所设扫描页数范围内查找主题文字。
- 从原文件名提取序列号，优先使用最后一个下划线后的数字；无法提取时使用文件名中最后一组数字。
- 按 `序列号_函件主题.pdf` 命名。例如：`扫描件_0096.pdf` 可预览为 `0096_关于设备改造的函.pdf`。
- 默认扫描首页；可在 GUI 中调整扫描页数和 OCR 分辨率。

### 2. 按首页最大字号重命名

- OCR 识别 PDF **首页上半部分**的文字。
- 仅保留近黑色/灰色文字，彩色印章等彩色内容会被过滤。
- 从最大字号文字开始，最多拼接 **1–3 行**相邻标题。
- 以拼接后的标题作为新文件名，保留原 PDF 扩展名。

> OCR 结果可能受扫描质量、版式、字体颜色等影响。正式重命名前，请先检查预览和生成的 CSV 报告。

## 使用图形界面

1. 在 Windows 中启动 `dist/PDF_Fax_Renamer.exe`。
2. 在“文件处理”页，分别为要运行的模式选择输入文件夹：
   - “主题行程序输入文件夹”对应传真主题模式。
   - “最大字号程序输入文件夹”对应首页最大字号模式。
3. 首次使用时保持“预览模式（不改名）”选中，点击相应模式的运行按钮。
4. 查看界面结果以及输入文件夹中的 CSV 报告；确认新文件名正确后，取消预览模式再运行。
5. 默认启用“备份原文件”。建议保留此选项，直到确认处理结果无误。

两种模式使用各自选择的输入文件夹和结果报告。递归处理子目录、备份选项和 OCR DPI 为共用选项；“主题程序页数”只影响传真主题模式。

### 处理结果和备份

| 模式 | 报告文件 | 默认备份目录 |
|---|---|---|
| 传真主题 | `rename_report.csv` | `_backup` |
| 首页最大字号 | `rename_by_largest_font_report.csv` | `_backup_largest_font` |

同名目标文件不会被覆盖，程序会自动为新名称追加序号。勾选预览模式时不会重命名或备份 PDF，但会生成报告。

## OCR 识别设置

打开“识别设置”页可编辑并保存：

### OCR 纠错词典

以 JSON 对象填写，键为 OCR 可能识别出的错误文本，值为替换文本。替换值为空字符串表示删除该文本。默认词条示例：

```json
{
  "关关于": "关于",
  "关干": "关于",
  "Subject:": "",
  "iSubject：": "",
  "头关于": ""
}
```

程序按较长的错误文本优先替换；英文大小写不敏感。可按实际 OCR 错误增加词条。

### SUBJECT_KEYS

每行填写一个主题标签关键词，默认值为：

```text
subject
主题
事由
关于
```

增加关键词后点击“保存识别设置”，后续传真主题模式会将其用于识别主题标签。保存时至少保留一个非空关键词。

配置保存在当前 Windows 用户的：

```text
%APPDATA%\PDF_Fax_Renamer\
```

文件名为 `name_corrections.json` 和 `subject_keys.json`。在 Linux/WSL 下，配置目录为 `$XDG_CONFIG_HOME/PDF_Fax_Renamer/`；若未设置 `XDG_CONFIG_HOME`，则使用 `~/.config/PDF_Fax_Renamer/`。这些配置独立于 EXE，升级程序时可继续使用。

## 命令行运行源码

需先安装 Python 3.11 和项目依赖：

```bash
python -m pip install -r requirements.txt
```

### 传真主题模式

```bash
python rename_by_subject.py "/path/to/pdf-folder" --dry-run
python rename_by_subject.py "/path/to/pdf-folder" --pages 1 --dpi 600
```

### 首页最大字号模式

```bash
python rename_by_largest_font.py "/path/to/pdf-folder" --dry-run
python rename_by_largest_font.py "/path/to/pdf-folder" --recursive --dpi 400
```

两个命令均支持 `--dry-run`、`--no-backup`、`--recursive`。主题模式另支持 `--pages N`；最大字号模式支持 `--dpi N`。默认执行会实际重命名文件并备份原件；确认预览无误后再省略 `--dry-run`。

WSL 下请优先使用 Linux 路径，例如 `/mnt/d/扫描文件`。最大字号脚本也支持将 Windows 盘符路径转换为 WSL 路径。

## 重新打包 Windows 单文件 EXE

在 Windows 中安装 Python 3.11 或 3.12 后，于项目目录运行：

```bat
build_exe.bat
```

构建脚本会创建或复用 `.build_venv`、安装 `requirements.txt` 和 PyInstaller，并生成：

```text
dist\PDF_Fax_Renamer.exe
```

该构建命令使用 `--onefile --windowed`，将 GUI 和两个重命名模块打包进同一个 EXE。

## 项目文件

- `rename_gui.py`：图形界面入口。
- `rename_by_subject.py`：传真主题提取与重命名。
- `rename_by_largest_font.py`：首页上半部分最大字号标题提取与重命名。
- `name_corrections.py`：OCR 纠错词典及 `SUBJECT_KEYS` 配置读写。
- `requirements.txt`：PDF、OCR 和图像处理依赖。
- `build_exe.bat`、`PDF_Fax_Renamer.spec`：Windows EXE 构建配置。
