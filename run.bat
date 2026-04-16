@echo off
REM Launcher for Buonaiuto Doc4LLM (Windows)
REM Prompts for which mode to start, then runs it.

setlocal

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"

if "%PYTHON_BIN%"=="" (
    where python >nul 2>nul
    if errorlevel 1 (
        where py >nul 2>nul
        if errorlevel 1 (
            echo Error: no Python interpreter found. Set PYTHON_BIN env var.
            exit /b 1
        ) else (
            set "PYTHON_BIN=py"
        )
    ) else (
        set "PYTHON_BIN=python"
    )
)

set "PYTHONPATH=%BASE_DIR%\src;%PYTHONPATH%"

echo Buonaiuto Doc4LLM -- choose a mode:
echo.
echo   1) MCP stdio server only          (for LLM clients; no website)
echo   2) MCP stdio server + dashboard   (website at http://127.0.0.1:8420)
echo   3) Dashboard only                 (website at http://127.0.0.1:8420)
echo   4) Watch docs_center\ for changes (auto re-scan)
echo.

set /p choice="Enter choice [1-4]: "

if "%choice%"=="1" (
    "%PYTHON_BIN%" -m buonaiuto_doc4llm --base-dir "%BASE_DIR%" serve
) else if "%choice%"=="2" (
    "%PYTHON_BIN%" -m buonaiuto_doc4llm --base-dir "%BASE_DIR%" serve --dashboard
) else if "%choice%"=="3" (
    "%PYTHON_BIN%" -m buonaiuto_doc4llm --base-dir "%BASE_DIR%" dashboard
) else if "%choice%"=="4" (
    "%PYTHON_BIN%" -m buonaiuto_doc4llm --base-dir "%BASE_DIR%" watch
) else (
    echo Invalid choice: %choice%
    exit /b 1
)

endlocal
