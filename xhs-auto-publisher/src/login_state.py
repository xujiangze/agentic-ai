from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 登录状态管理类，用于持久化存储和验证小红书登录状态


class LoginState:
    # 初始化登录状态管理器
    def __init__(self, runtime_dir: Path, *, cache_hours: int = 12, account: str = "default") -> None:
        self.runtime_dir = runtime_dir
        self.cache_hours = cache_hours
        self.account = account
        self.path = runtime_dir / "login_cache.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    # 验证缓存中的登录状态是否有效
    def is_valid(self) -> bool:
        # 读取缓存数据
        payload = self._read()
        if not payload:
            return False
        # 验证平台和账户信息是否匹配
        if payload.get("platform") != "xiaohongshu" or payload.get("account") != self.account:
            return False
        # 检查是否已登录
        if not payload.get("is_logged_in"):
            return False
        # 解析过期时间并与当前时间比较
        expires_at = _parse_dt(payload.get("expires_at"))
        return bool(expires_at and expires_at > datetime.now(timezone.utc).astimezone())

    # 标记为已登录状态，并写入缓存
    def mark_logged_in(self, *, home_url: str) -> None:
        # 获取当前时间并构建登录状态数据
        now = datetime.now(timezone.utc).astimezone()
        payload = {
            "platform": "xiaohongshu",
            "account": self.account,
            "is_logged_in": True,
            "checked_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=self.cache_hours)).isoformat(),
            "home_url": home_url,
        }
        # 将登录状态写入文件
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 使登录状态失效（标记为未登录）
    def invalidate(self) -> None:
        payload = self._read() or {}
        payload.update(
            {
                "platform": "xiaohongshu",
                "account": self.account,
                "is_logged_in": False,
                "checked_at": datetime.now(timezone.utc).astimezone().isoformat(),
            }
        )
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 读取缓存文件，返回解析后的数据或 None
    def _read(self) -> dict[str, Any] | None:
        # 文件不存在时返回 None
        if not self.path.exists():
            return None
        # 尝试解析 JSON 文件
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None
        # 确保返回的是字典类型
        return payload if isinstance(payload, dict) else None


# 辅助函数：解析 ISO 格式的时间字符串
def _parse_dt(value: Any) -> datetime | None:
    # 空值直接返回 None
    if not value:
        return None
    # 尝试解析 ISO 格式的时间字符串
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # 如果时间对象没有时区信息，默认添加 UTC 时区
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone()
    # 转换为本地时区
    return dt.astimezone()
