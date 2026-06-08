# 浏览器会话管理类 - 管理 Playwright 浏览器的生命周期和事件监控
from __future__ import annotations

from pathlib import Path
from typing import Any


class BrowserSession:
    # 初始化浏览器会话配置
    def __init__(
        self,
        *,
        profile_dir: Path,              # 浏览器用户配置目录路径
        headless: bool,                  # 是否无头模式运行
        slow_mo_ms: int = 80,            # 操作延迟（毫秒），模拟真实用户操作速度
        audit: Any = None,               # 审计对象，用于记录调试事件
        debug_url_keywords: list[str] | None = None,  # 需要监控的 URL 关键词列表
    ) -> None:
        self.profile_dir = profile_dir
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.audit = audit
        self.debug_url_keywords = [k.lower() for k in (debug_url_keywords or [])]
        self.playwright: Any = None
        self.context: Any = None
        self.page: Any = None

    # 异步上下文管理器入口 - 启动浏览器并初始化会话
    async def __aenter__(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "playwright is not installed. Install it with: pip install playwright && python -m playwright install chromium"
            ) from exc

        # 创建配置目录（如不存在）
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # 启动 Playwright 实例
        self.playwright = await async_playwright().start()
        # 启动持久化浏览器上下文（保持登录状态和 Cookie）
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),  # 用户数据目录
            headless=self.headless,              # 无头模式
            slow_mo=self.slow_mo_ms,              # 操作延迟
            viewport={"width": 1440, "height": 1000},  # 浏览器窗口大小
            args=["--disable-blink-features=AutomationControlled"],  # 禁用自动化检测特征
        )
        # 获取或创建页面标签
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        # 如果启用了审计模式，附加调试钩子
        if self.audit:
            self._attach_debug_hooks()
        return self

    # 附加调试钩子 - 监控浏览器事件以进行调试和审计
    def _attach_debug_hooks(self) -> None:
        if not self.page or not self.audit:
            return

        # 文本截断辅助函数 - 防止日志过长
        def short(text: Any, limit: int = 500) -> str:
            s = str(text or "")
            return s if len(s) <= limit else s[:limit] + "..."

        # 控制台消息处理 - 监控浏览器控制台输出
        def on_console(msg: Any) -> None:
            try:
                level = msg.type
                text = short(msg.text)
                # 只记录错误、警告或包含关键词的消息
                if level in {"error", "warning"} or any(k in text.lower() for k in ["publish", "fail", "error", "接口", "失败", "问题"]):
                    self.audit.event("console_message", level=level, text=text)
            except Exception:
                pass

        # 页面错误处理 - 监控页面运行时错误
        def on_page_error(err: Any) -> None:
            try:
                self.audit.event("page_error", error=short(err, 1000))
            except Exception:
                pass

        # URL 过滤判断 - 检查 URL 是否包含感兴趣的关键词
        def is_interesting_url(url: str) -> bool:
            lowered = url.lower()
            # 基础关键词列表：发布、笔记、上传、创作、草稿、API 等
            base = ["publish", "note", "post", "upload", "creator", "draft", "web_api", "api/sns"]
            keys = base + self.debug_url_keywords  # 合并用户自定义关键词
            return any(k in lowered for k in keys)

        # HTTP 请求处理 - 监控发出的网络请求
        def on_request(req: Any) -> None:
            try:
                url = str(req.url)
                # 只记录感兴趣的 URL
                if not is_interesting_url(url):
                    return
                # 对于 POST/PUT/PATCH 请求，记录请求体数据
                post_data = req.post_data if req.method.upper() in {"POST", "PUT", "PATCH"} else None
                self.audit.event(
                    "request_seen",
                    method=req.method,
                    resource_type=req.resource_type,
                    url=short(url, 800),
                    post_data=short(post_data, 1200) if post_data else None,
                )
            except Exception:
                pass

        # 请求失败处理 - 监控失败的 HTTP 请求
        def on_request_failed(req: Any) -> None:
            try:
                failure = req.failure() or {}
                self.audit.event(
                    "request_failed",
                    method=req.method,
                    url=short(req.url, 800),
                    resource_type=req.resource_type,
                    error_text=failure.get("errorText", ""),
                )
            except Exception:
                pass

        # HTTP 响应处理 - 监控接收到的网络响应
        def on_response(resp: Any) -> None:
            try:
                url = str(resp.url)
                # 记录感兴趣的 URL 或错误状态码（4xx、5xx）
                interesting = is_interesting_url(url) or resp.status >= 400
                if not interesting:
                    return
                self.audit.event(
                    "response_seen",
                    status=resp.status,
                    url=short(url, 800),
                )
            except Exception:
                pass

        # 请求完成处理 - 监控请求完成后的最终状态和响应体
        async def on_request_finished(req: Any) -> None:
            try:
                url = str(req.url)
                if not is_interesting_url(url):
                    return
                resp = await req.response()
                status = resp.status if resp else None
                body_text = None
                # 对于错误响应，记录响应体内容（便于调试）
                if resp and status and status >= 400:
                    try:
                        body_text = short(await resp.text(), 1500)
                    except Exception:
                        body_text = None
                self.audit.event(
                    "request_finished",
                    method=req.method,
                    url=short(url, 800),
                    status=status,
                    response_text=body_text,
                )
            except Exception:
                pass

        # 注册所有事件监听器到页面
        self.page.on("console", on_console)
        self.page.on("pageerror", on_page_error)
        self.page.on("request", on_request)
        self.page.on("requestfailed", on_request_failed)
        self.page.on("response", on_response)
        self.page.on("requestfinished", on_request_finished)

    # 异步上下文管理器出口 - 清理资源并关闭浏览器
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # 关闭浏览器上下文（保存状态）
        if self.context:
            await self.context.close()
        # 停止 Playwright 实例
        if self.playwright:
            await self.playwright.stop()
