from __future__ import annotations

import importlib.util
import subprocess
import sys
import traceback
from pathlib import Path
from tkinter import messagebox

APP_DIR = Path(__file__).resolve().parent
REQUIREMENTS_PATH = APP_DIR / "requirements.txt"
ERROR_LOG_PATH = APP_DIR / "video_manager_error.log"
REQUIRED_MODULES = ("aiohttp", "ttkbootstrap", "bilibili_api")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_missing_modules() -> list[str]:
    return [
        module_name
        for module_name in REQUIRED_MODULES
        if importlib.util.find_spec(module_name) is None
    ]


def install_dependencies() -> bool:
    missing_modules = find_missing_modules()
    if not missing_modules:
        return True

    if not REQUIREMENTS_PATH.exists():
        messagebox.showerror("缺少 requirements.txt", "未找到 requirements.txt，无法自动安装依赖。")
        return False

    should_install = messagebox.askyesno(
        "安装依赖",
        "检测到缺少运行依赖："
        f"{', '.join(missing_modules)}\n\n是否现在自动安装？",
    )
    if not should_install:
        return False

    process = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        check=False,
        creationflags=CREATE_NO_WINDOW,
    )
    if process.returncode == 0:
        return True

    details = process.stderr.strip() or process.stdout.strip() or "依赖安装失败。"
    messagebox.showerror("安装失败", f"自动安装依赖失败：\n\n{details[:1200]}")
    return False


def write_error_log(details: str) -> Path | None:
    try:
        ERROR_LOG_PATH.write_text(details.strip() + "\n", encoding="utf-8")
    except OSError:
        return None
    return ERROR_LOG_PATH


def main() -> None:
    if not install_dependencies():
        return

    try:
        from app import main as run_app
    except Exception:
        error_log_path = write_error_log(traceback.format_exc())
        log_hint = f"\n\n错误日志：{error_log_path}" if error_log_path else ""
        messagebox.showerror("启动失败", f"加载程序时发生异常。{log_hint}")
        return

    try:
        run_app()
    except Exception:
        error_log_path = write_error_log(traceback.format_exc())
        log_hint = f"\n\n错误日志：{error_log_path}" if error_log_path else ""
        messagebox.showerror("程序异常", f"下载器运行时发生异常。{log_hint}")


if __name__ == "__main__":
    main()
