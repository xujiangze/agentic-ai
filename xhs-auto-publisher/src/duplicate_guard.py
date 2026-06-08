# 启用类型注解的延迟求值（用于 Python < 3.11）
from __future__ import annotations

# 标准库导入
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class DuplicateGuard:
    """防止重复发布的安全守卫类。

    职责：
    1. 检测内容指纹是否重复（防止相同内容发布两次）
    2. 强制最小发布间隔（防止频繁发布）
    3. 记录发布历史到 JSON 文件
    """
    def __init__(self, runtime_dir: Path, *, min_interval_minutes: int = 10) -> None:
        """初始化守卫实例。

        Args:
            runtime_dir: 运行时目录，用于存储发布历史 JSON 文件
            min_interval_minutes: 两次发布之间的最小间隔时间（默认 10 分钟）
        """
        self.path = runtime_dir / "published_history.json"
        self.min_interval_minutes = min_interval_minutes
        runtime_dir.mkdir(parents=True, exist_ok=True)

    def check(self, fingerprint: str) -> None:
        """检查是否允许发布。

        检查规则：
        1. 内容指纹不得重复（相同内容已发布过则拒绝）
        2. 距离上次发布时间不得少于 min_interval_minutes

        Args:
            fingerprint: 内容的唯一标识符（通常是内容哈希值）

        Raises:
            RuntimeError: 当指纹重复或发布间隔未过时抛出
        """
        payload = self._read()
        entries = payload.get("entries", [])
        if any(entry.get("fingerprint") == fingerprint for entry in entries):
            raise RuntimeError("duplicate content fingerprint; refusing to publish the same post twice")

        last_published_at = _parse_dt(payload.get("last_published_at"))
        if last_published_at:
            next_allowed = last_published_at + timedelta(minutes=self.min_interval_minutes)
            if datetime.now(timezone.utc).astimezone() < next_allowed:
                raise RuntimeError(f"publish interval guard active; next publish allowed after {next_allowed.isoformat()}")

    def record(self, fingerprint: str, result: dict[str, Any]) -> None:
        """记录一次成功的发布。

        Args:
            fingerprint: 内容的唯一标识符
            result: 发布结果字典（包含发布 ID、URL 等元数据）

        注意：
            - 只保留最近 200 条记录（防止文件过大）
            - 自动更新"最后发布时间"戳
        """
        payload = self._read()
        now = datetime.now(timezone.utc).astimezone().isoformat()
        entries = payload.get("entries", [])
        entries.append(
            {
                "fingerprint": fingerprint,
                "published_at": now,
                "result": result,
            }
        )
        payload["last_published_at"] = now
        payload["entries"] = entries[-200:]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read(self) -> dict[str, Any]:
        """读取发布历史文件。

        Returns:
            包含 "entries" 和 "last_published_at" 的字典

        容错处理：
            - 文件不存在时返回空结构
            - JSON 解析失败时返回空结构
            - 数据格式错误时返回空结构
        """
        if not self.path.exists():
            return {"entries": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"entries": []}
        return payload if isinstance(payload, dict) else {"entries": []}


def _parse_dt(value: Any) -> datetime | None:
    """解析 ISO 格式的日期时间字符串。

    Args:
        value: ISO 格式的时间字符串（如 "2024-01-15T10:30:00+08:00"）

    Returns:
        时区感知的 datetime 对象，解析失败时返回 None

    容错处理：
        - 空值返回 None
        - 无效格式返回 None
        - 无时区信息时假定为 UTC
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone()
    return dt.astimezone()
