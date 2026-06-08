from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 获取当前时间戳，用于生成唯一的文件名
def now_stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")


# 审计日志类：用于记录自动化操作过程中的所有事件、截图和 DOM 快照
class AuditLog:
    def __init__(self, run_dir: Path) -> None:
        # 初始化审计日志，设置运行目录和子目录结构
        self.run_dir = run_dir
        self.screenshots_dir = run_dir / "screenshots"  # 截图存储目录
        self.dom_dir = run_dir / "dom"  # DOM 快照存储目录
        self.actions_path = run_dir / "actions.jsonl"  # 事件日志文件
        # 创建所有必要的目录
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.dom_dir.mkdir(parents=True, exist_ok=True)

    # 记录一个事件到审计日志，包含时间戳和动作类型
    def event(self, action: str, **fields: Any) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),  # 当前时间戳
            "action": action,  # 动作类型
            **fields,  # 附加字段
        }
        # 以追加模式写入 JSONL 文件
        with self.actions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    # 截取页面截图并记录到审计日志
    async def screenshot(self, page: Any, name: str, *, full_page: bool = True) -> Path:
        path = self.screenshots_dir / f"{name}.png"
        try:
            await page.screenshot(path=str(path), full_page=full_page)
            self.event("screenshot", name=name, path=str(path))  # 记录成功事件
        except Exception as exc:  # noqa: BLE001 - 审计日志不应阻断主流程
            self.event("screenshot_failed", name=name, error=str(exc))  # 记录失败事件
        return path

    # 保存页面 DOM 快照到 HTML 文件
    async def dom_snapshot(self, page: Any, name: str) -> Path:
        path = self.dom_dir / f"{name}.html"
        try:
            content = await page.content()  # 获取页面 HTML 内容
            path.write_text(content, encoding="utf-8")  # 保存到文件
            self.event("dom_snapshot", name=name, path=str(path))  # 记录成功事件
        except Exception as exc:  # noqa: BLE001
            self.event("dom_snapshot_failed", name=name, error=str(exc))  # 记录失败事件
        return path

    # 将任意数据序列化为 JSON 文件并记录事件
    def write_json(self, name: str, payload: Any) -> Path:
        path = self.run_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.event("write_json", name=name, path=str(path))  # 记录写入事件
        return path
