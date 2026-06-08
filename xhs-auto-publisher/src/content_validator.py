from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 自定义异常类，用于内容验证失败时抛出错误
class ContentValidationError(ValueError):
    pass


# 小红书内容数据类（不可变），封装笔记的元数据和内容
@dataclass(frozen=True)
class XhsContent:
    title: str           # 笔记标题
    body: str            # 笔记正文
    topics: list[str]    # 话题标签列表
    images: list[Path]   # 图片文件路径列表
    mode: str            # 发布模式："draft"（草稿）或"publish"（发布）
    source_path: Path    # 内容来源的 JSON 文件路径

    # 属性：将正文和话题标签组合成完整的笔记文本
    @property
    def body_with_topics(self) -> str:
        # 将所有话题标签格式化为 "#话题1 #话题2" 的形式
        tags = " ".join(f"#{topic}" for topic in self.topics)
        if not tags:
            return self.body
        return f"{self.body.rstrip()}\n\n{tags}"

    # 属性：生成内容的唯一指纹（SHA-256 哈希），用于检测重复内容
    # 基于标题、正文和图片（路径+大小）计算哈希值
    @property
    def fingerprint(self) -> str:
        h = hashlib.sha256()
        h.update(self.title.encode("utf-8"))
        h.update(b"\0")
        h.update(self.body.encode("utf-8"))
        for image in self.images:
            h.update(b"\0")
            h.update(str(image.resolve()).encode("utf-8"))
            if image.exists():
                # 将文件大小也纳入哈希，确保图片修改后指纹变化
                h.update(str(image.stat().st_size).encode("utf-8"))
        return h.hexdigest()

    # 方法：将内容对象转换为可 JSON 序列化的字典
    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "topics": self.topics,
            "images": [str(path) for path in self.images],
            "mode": self.mode,
            "fingerprint": self.fingerprint,
            "source_path": str(self.source_path),
        }


# 函数：从 JSON 文件加载并验证小红书内容
# 参数 path：JSON 文件路径
# 参数 mode_override：可选，强制覆盖发布模式（"draft" 或 "publish"）
def load_content(path: Path, *, mode_override: str | None = None) -> XhsContent:
    # 第一步：检查文件是否存在
    if not path.exists():
        raise ContentValidationError(f"content file does not exist: {path}")
    # 第二步：解析 JSON
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ContentValidationError("content JSON root must be an object")

    # 第三步：提取并规范化字段值
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    topics = payload.get("topics") or []
    images = payload.get("images") or []
    # mode 优先级：命令行覆盖 > 文件中指定 > 默认 "publish"
    mode = str(mode_override or payload.get("mode") or "publish").strip().lower()

    # 第四步：验证字段内容是否符合小红书平台规则
    if not title:
        raise ContentValidationError("title is required")
    if len(title) > 80:
        raise ContentValidationError("title is too long; keep it within 80 chars")
    if not body:
        raise ContentValidationError("body is required")
    if len(body) > 2000:
        raise ContentValidationError("body is too long; keep it within 2000 chars")
    if mode not in {"draft", "publish"}:
        raise ContentValidationError("mode must be draft or publish")
    if not isinstance(topics, list) or any(not str(topic).strip() for topic in topics):
        raise ContentValidationError("topics must be a list of non-empty strings")
    if not isinstance(images, list):
        raise ContentValidationError("images must be a list")
    if mode == "publish" and not images:
        raise ContentValidationError("publish mode requires at least one image")

    # 第五步：验证图片路径是否存在且格式正确
    base_dir = path.parent
    image_paths: list[Path] = []
    for raw in images:
        image_path = Path(str(raw))
        # 如果是相对路径，则相对于 JSON 文件所在目录解析
        if not image_path.is_absolute():
            image_path = (base_dir / image_path).resolve()
        if not image_path.exists():
            raise ContentValidationError(f"image does not exist: {image_path}")
        # 只支持常见的图片格式
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ContentValidationError(f"unsupported image type: {image_path}")
        image_paths.append(image_path)

    # 第六步：清理话题标签（移除前导 "#" 号），构造并返回内容对象
    clean_topics = [str(topic).strip().lstrip("#") for topic in topics]
    return XhsContent(
        title=title,
        body=body,
        topics=clean_topics,
        images=image_paths,
        mode=mode,
        source_path=path,
    )
