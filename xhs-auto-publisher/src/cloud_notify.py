from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 云端通知器：负责将登录二维码等通知信息传递给外部服务（如飞书群）
class CloudNotifier:
    # 初始化通知器，保存应用配置
    def __init__(self, app_config: dict[str, Any]) -> None:
        self.app_config = app_config

    # 检查是否启用了二维码交接通知功能
    # 通过配置项 notify_qr_via 判断，当前支持 lobster_channel 模式
    def qr_handoff_enabled(self) -> bool:
        mode = str(self.app_config.get("notify_qr_via", "none")).lower()
        return mode == "lobster_channel"

    # 发送二维码通知到云端服务
    # 截图路径会传递给外部服务（如飞书机器人），引导用户扫码登录
    def notify_qr(self, screenshot_path: Path, *, run_dir: Path) -> None:
        mode = str(self.app_config.get("notify_qr_via", "none")).lower()
        # 检查通知模式是否支持
        if mode != "lobster_channel":
            raise RuntimeError(f"Unsupported cloud notify mode: {mode}")
        # 生成并发送载荷到 lobster_channel
        self._emit_lobster_channel_payload(screenshot_path, run_dir=run_dir)

    # 生成 lobster_channel 的载荷文件
    # 该载荷会被外部服务读取，将二维码图片发送到飞书群
    def _emit_lobster_channel_payload(self, screenshot_path: Path, *, run_dir: Path) -> None:
        # 获取通知目录，用于存放载荷文件
        notify_dir = self._notify_dir(run_dir)
        notify_dir.mkdir(parents=True, exist_ok=True)
        # 构建载荷数据结构，包含时间戳、平台信息、截图路径等
        payload = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(),
            "channel": "lobster_channel",
            "kind": "login_qr",
            "platform": str(self.app_config.get("platform", "xiaohongshu")),
            "title": f"{self._title_prefix()} 小红书登录二维码",
            "run_id": run_dir.name,
            "screenshot_path": str(screenshot_path),
            "message_lines": self._build_message_lines(screenshot_path, run_dir=run_dir),
            "action": "send_image_to_feishu_group",
            "delivery": {
                "type": "image_file",
                "path": str(screenshot_path),
                "caption_lines": self._build_message_lines(screenshot_path, run_dir=run_dir),
            },
        }
        # 将载荷写入 JSON 文件，外部服务会监听此文件并执行发送操作
        path = notify_dir / "login_qr.payload.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 计算通知文件的存放目录
    # 支持相对路径（相对于项目根目录）和绝对路径
    def _notify_dir(self, run_dir: Path) -> Path:
        # 从配置读取目录，默认为 runtime/lobster-notify
        configured = str(self.app_config.get("lobster_notify_dir", "runtime/lobster-notify")).strip()
        base = Path(configured)
        # 如果是相对路径，则从项目根目录解析
        if not base.is_absolute():
            base = run_dir.parent.parent / base.name
        # 返回包含当前运行 ID 的目录路径
        return base / run_dir.name

    # 获取飞书通知的标题前缀
    # 用于标识通知消息的来源，默认为 [XHS Cloud Login]
    def _title_prefix(self) -> str:
        return str(self.app_config.get("feishu_title_prefix", "[XHS Cloud Login]")).strip() or "[XHS Cloud Login]"

    # 构建飞书通知的消息内容
    # 包含标题、运行 ID、图片路径和操作指引
    def _build_message_lines(self, screenshot_path: Path, *, run_dir: Path) -> list[str]:
        return [
            f"{self._title_prefix()} 小红书登录二维码",
            f"Run ID: {run_dir.name}",
            f"图片路径: {screenshot_path}",
            "请把这张二维码图片直接发到飞书群，用户扫码后等待任务继续。",
        ]
