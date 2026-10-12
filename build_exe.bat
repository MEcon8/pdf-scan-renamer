@echo off
chcp 65001 >nul
setlocal

echo ========================================
echo PDF Fax Renamer - one-click build
echo Author: 005542
echo ========================================
echo.

set "BASE_PYTHON="
where py >nul 2>&1
if not errorlevel 1 (
	py -3.11 -c "import sys" >nul 2>&1
	if not errorlevel 1 set "BASE_PYTHON=py -3.11"
	if not defined BASE_PYTHON (
		py -3.12 -c "import sys" >nul 2>&1
		if not errorlevel 1 set "BASE_PYTHON=py -3.12"
	)
)
if not defined BASE_PYTHON (
	python -c "import sys" >nul 2>&1
	if not errorlevel 1 set "BASE_PYTHON=python"
)

if not defined BASE_PYTHON (
	echo Python was not found. Trying to install Python 3.11 with winget...
	where winget >nul 2>&1
	if errorlevel 1 (
		echo winget is not available. Please install Python 3.11 or 3.12 manually.
		goto fail
	)
	winget install --id Python.Python.3.11 --exact --scope user --accept-source-agreements --accept-package-agreements
	if errorlevel 1 goto fail

	if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
		set "BASE_PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
	) else (
		echo Python was installed, but its executable could not be located.
		echo Please reopen this script after restarting the terminal.
		goto fail
	)
)

echo [1/5] Checking Python...
%BASE_PYTHON% --version
if errorlevel 1 goto fail

if not exist ".build_venv\Scripts\python.exe" (
	echo [2/5] Creating isolated build environment...
	%BASE_PYTHON% -m venv .build_venv
	if errorlevel 1 goto fail
) else (
	echo [2/5] Reusing .build_venv...
)

set "PYTHON=.build_venv\Scripts\python.exe"

echo [3/5] Upgrading pip...
%PYTHON% -m pip install --upgrade pip
if errorlevel 1 goto fail

echo [4/5] Installing OCR, PDF and image dependencies...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 goto fail
%PYTHON% -m pip install pyinstaller
if errorlevel 1 goto fail

echo [5/5] Building executable...
%PYTHON% -m PyInstaller --noconfirm --clean --onefile --windowed --name PDF_Fax_Renamer --collect-all rapidocr_onnxruntime --collect-all onnxruntime --collect-submodules pymupdf --hidden-import rename_by_subject --hidden-import rename_by_largest_font --hidden-import name_corrections rename_gui.py
if errorlevel 1 goto fail

echo.
echo ========================================
echo Build complete:
echo dist\PDF_Fax_Renamer.exe
echo ========================================
pause
exit /b 0

:fail
echo.
echo Build failed. Please read the error above.
pause
exit /b 1
