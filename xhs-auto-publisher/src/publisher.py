"""
小红书自动发布器核心模块
======================

本模块实现小红书图文笔记的自动化发布，涵盖登录、内容填充、图片上传、发布确认的完整流程。

整体架构
--------
XhsPublisher 是一个纯业务编排层，本身不管理浏览器生命周期，只接收一个 Playwright page 对象
（由 BrowserSession 提供），专注于发布流程的状态机控制。

调用链路::

    publish_xhs.py（入口）
        └── BrowserSession（浏览器生命周期）
                └── XhsPublisher(page=session.page, ...)  ← 本模块
                        ├── ensure_login()       登录保障
                        ├── open_publish_page()   打开发布页
                        ├── fill_note()           填充内容
                        └── click_publish_and_wait()  发布确认

核心设计原则
------------
1. 多策略降级（Multi-Strategy Fallback）
   小红书前端频繁改版，CSS 类名和 DOM 结构不稳定。几乎所有 DOM 操作都采用
   "策略1 → 策略2 → ... → 最终兜底" 的降级模式，而不是依赖单一选择器。
   典型例子：click_bottom_publish_button 有 4 层降级策略。

2. 评分制按钮定位（Score-Based Button Detection）
   对于"发布"这类关键按钮，不依赖固定选择器，而是通过多维评分定位：
   - 文本匹配分、按钮角色分、尺寸合理性分、颜色分（红色优先）
   - 综合得分最高的候选元素被选为点击目标
   - 这使得即使 DOM 结构变化，只要按钮的视觉特征不变就能正确定位

3. 最小侵入式等待（Best-Effort Settle）
   使用 _best_effort_settle() 代替硬性 networkidle 等待，
   超时不报错而是静默降级到固定延时，避免长连接导致永久阻塞。

4. 防御性编程（Defensive Event Handling）
   所有事件监听器和 DOM 检测都用 try/except 包裹，
   确保检测逻辑的异常不会中断主发布流程。

5. 审计追踪（Audit Trail）
   每个关键步骤都通过 audit.event() 记录，配合截图和 DOM 快照，
   便于事后回溯发布失败的原因。

6. keyword-only 构造参数
   与 BrowserSession 一致，防止参数位置错乱。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .cloud_notify import CloudNotifier
from .content_validator import XhsContent
from .locator_utils import any_visible, click_first, fill_first, first_attached
from .login_state import LoginState


class XhsPublisher:
    """
    小红书发布器主类 - 编排登录、内容填充和发布的完整流程。

    职责边界
    --------
    - 负责：发布流程的状态机控制（登录 → 开页 → 填充 → 发布）
    - 不负责：浏览器生命周期管理（由 BrowserSession 负责）
    - 不负责：内容验证和格式化（由 XhsContent 在传入前完成）

    对外只暴露 run() 方法作为主入口。
    """
    def __init__(
        self,
        *,
        page: Any,
        app_config: dict[str, Any],
        selectors: dict[str, list[dict[str, Any]]],
        login_state: LoginState,
        audit: AuditLog,
        notifier: CloudNotifier,
        login_timeout_seconds: int = 180,
    ) -> None:
        """初始化发布器实例

        参数:
            page: Playwright 页面对象
            app_config: 应用配置(包含URL等)
            selectors: CSS选择器配置
            login_state: 登录状态管理器
            audit: 审计日志记录器
            notifier: 云通知服务(用于推送二维码)
            login_timeout_seconds: 登录超时时间(秒)
        """
        self.page = page
        self.app_config = app_config
        self.selectors = selectors
        self.login_state = login_state
        self.audit = audit
        self.notifier = notifier
        self.login_timeout_seconds = login_timeout_seconds

    async def run(self, content: XhsContent) -> dict[str, Any]:
        """执行发布流程的主入口

        状态机流程::

            ┌─────────────┐
            │ ensure_login │  ← 缓存命中则跳过，未命中则触发扫码
            └──────┬──────┘
                   ▼
            ┌──────────────────┐
            │ open_publish_page │  ← 访问发布 URL，二次验证登录态
            └──────┬───────────┘
                   ▼
            ┌────────────┐
            │ fill_note  │  ← 上传图片 + 填标题 + 填正文 + 验证
            └──────┬─────┘
                   ▼
            ┌─────────────────────────┐
            │ mode == "draft" ?       │
            │  是 → 返回 draft_ready  │
            │  否 → click_publish     │
            └─────────────────────────┘

        参数:
            content: 待发布的内容对象（已通过 XhsContent 验证和格式化）

        返回:
            包含 status/mode/url 等信息的字典
        """
        self.audit.event("publish_run_start", mode=content.mode, title=content.title)
        await self.ensure_login()
        await self.open_publish_page()
        await self.fill_note(content)
        await self.audit.screenshot(self.page, "before_publish")
        await self.audit.dom_snapshot(self.page, "before_publish")

        # 草稿模式: 仅填充内容，不点击发布
        if content.mode == "draft":
            result = {
                "status": "draft_ready",
                "mode": "draft",
                "url": self.page.url,
                "message": "Content filled and left before publish click.",
            }
            self.audit.event("draft_ready", url=self.page.url)
            return result

        # 正式发布模式
        result = await self.click_publish_and_wait()
        self.audit.event("publish_run_done", **result)
        return result

    async def ensure_login(self) -> None:
        """确保用户已登录

        三层登录策略::

            ┌─────────────────────────┐
            │ 1. 缓存检查              │  LoginState.is_valid()
            │    命中 → 直接返回        │  避免每次都访问主页
            └──────┬──────────────────┘
                   │ 未命中
                   ▼
            ┌─────────────────────────┐
            │ 2. 访问主页检测           │  page.goto(home_url)
            │    已登录 → 更新缓存      │  mark_logged_in()
            └──────┬──────────────────┘
                   │ 未登录
                   ▼
            ┌─────────────────────────┐
            │ 3. 二维码登录流程         │  prepare_qr_login()
            │    截图推送 → 轮询等待    │  wait_for_user_login()
            └─────────────────────────┘

        为什么不在步骤 2 直接跳到发布页？
        主页是公开页面，加载快且不依赖登录态；发布页如果未登录会被重定向，
        检测逻辑更复杂。先在主页确认登录状态更可靠。
        """
        home_url = str(self.app_config["creator_home_url"])
        # 缓存命中，无需重新登录
        if self.login_state.is_valid():
            self.audit.event("login_cache_hit")
            return

        # 缓存失效，访问主页检测
        self.audit.event("login_cache_miss", url=home_url)
        # DOMContentLoaded 是浏览器原生事件，触发时机为
        # HTML 文档被完全加载和解析完成，DOM 树构建完毕，但此时外部资源（如图片、样式表、iframe、字体等）可能仍在加载中。
        # 由于课程中只要dom完成即可, 因此无需等待其他的
        # 模式: domcontentloaded
        #   - 触发时机: DOM 树构建完成，但外部资源可能还在加载
        #   - 适用场景: 页面主要内容是 HTML 文本，依赖 JS 渲染较少的情况；或者你需要尽快获取页面标题/URL 等元数据。
        # 模式: load
        #   - 触发时机: 所有资源（图片、CSS、字体、脚本等）都加载完成。
        #   - 适用场景: 传统多页面应用（MPA），需要等待所有可见资源加载完再操作。
        # 模式: networkidle (不推荐)
        #   - 触发时机: 网络空闲（无活跃请求），通常指 500ms 内没有新的网络请求。但可能等待过久或永不触发（如长轮询）。
        #   - 适用场景: 单页应用（SPA）的粗暴等待方式，但易不稳定，建议改用更精确的选择器等待。
        # 模式: commit
        #   - 触发时机: 收到响应头（HTTP 状态码、headers）并开始解析，但 HTML 可能还没收到。极少用。
        #   - 适用场景: 	只需要确认请求已发送并收到初步响应，不关心内容。
        await self.page.goto(home_url, wait_until="domcontentloaded")
        await self._best_effort_settle(20_000)
        await self.audit.screenshot(self.page, "login_check")

        # 检测页面是否显示登录状态
        if await self._looks_logged_in():
            self.login_state.mark_logged_in(home_url=home_url)
            self.audit.event("login_confirmed")
            return

        # 未登录，触发二维码登录流程
        self.audit.event("login_required", timeout_seconds=self.login_timeout_seconds)
        await self.prepare_qr_login()
        await self.wait_for_user_login()
        self.login_state.mark_logged_in(home_url=home_url)
        self.audit.event("login_confirmed_after_handoff")

    async def wait_for_user_login(self) -> None:
        """轮询等待用户扫码登录完成

        轮询策略:
        - 每秒检测一次登录状态（通过 _looks_logged_in 检测页面元素）
        - 每 30 秒截图一次（便于事后查看扫码进度）
        - 超时后记录最终状态并抛出 TimeoutError

        为什么不监听网络请求来判断登录成功？
        小红书的登录接口可能变化，且可能有多种登录回调 URL，
        基于页面元素检测比依赖特定 API 更稳定。
        """
        deadline = self.login_timeout_seconds
        for second in range(deadline):
            if await self._looks_logged_in():
                return
            # 每30秒截图记录等待状态
            if second and second % 30 == 0:
                await self.audit.screenshot(self.page, f"login_wait_{second}s")
            await self.page.wait_for_timeout(1000)
        # 超时: 记录最后状态并抛出异常
        await self.audit.screenshot(self.page, "login_timeout")
        await self.audit.dom_snapshot(self.page, "login_timeout")
        raise TimeoutError("login was not completed before timeout")

    async def prepare_qr_login(self) -> None:
        """准备二维码登录

        流程:
        1. 尝试点击二维码切换按钮（多个候选选择器，适配不同版本的小红书登录页）
        2. 截取二维码图片保存到审计目录
        3. 通过 CloudNotifier 推送二维码给用户（如果启用了远程通知）

        为什么需要多个候选选择器？
        小红书登录页有多种布局版本，二维码元素的选择器不固定。
        使用 candidates 列表依次尝试，找到哪个算哪个。
        """
        clicked = False
        candidates = [
            "img.css-wemwzq",
            "img[src^='data:image/png']",
            "[class*='qrcode']",
            "[class*='qr']",
        ]
        # 尝试多个选择器找到二维码切换按钮
        for selector in candidates:
            try:
                locator = self.page.locator(selector).first
                await locator.wait_for(state="visible", timeout=1500)
                await locator.click()
                clicked = True
                self.audit.event("qr_login_toggle_clicked", selector=selector)
                break
            except Exception:
                continue

        await self.page.wait_for_timeout(1200)
        qr_path = await self.audit.screenshot(self.page, "login_qr")
        await self.audit.dom_snapshot(self.page, "login_qr")
        # 推送二维码到云通知服务
        if self.notifier.qr_handoff_enabled():
            self.notifier.notify_qr(qr_path, run_dir=self.audit.run_dir)
        print(f"LOGIN_QR_SCREENSHOT={qr_path}", flush=True)
        self.audit.event("login_qr_screenshot_ready", path=str(qr_path), clicked=clicked)

    async def open_publish_page(self) -> None:
        """打开发布页面

        为什么需要二次验证登录状态？
        ensure_login() 通过后，用户可能在主页和发布页之间的跳转中被踢出登录
        （如 Cookie 过期、服务端主动失效）。在发布页上再次检测可以防止
        在未登录状态下填充内容导致数据丢失。

        流程:
        1. 访问发布页 URL
        2. 二次验证登录状态（如失效则 invalidate 缓存并重新走 ensure_login）
        3. 切换到"图文上传"标签（默认可能是视频上传标签）
        """
        publish_url = str(self.app_config["publish_url"])
        self.audit.event("open_publish_page", url=publish_url)
        await self.page.goto(publish_url, wait_until="domcontentloaded")
        await self._best_effort_settle(30_000)

        # 二次验证登录状态
        if not await self._looks_logged_in():
            self.audit.event("login_cache_invalidated_on_publish_page")
            self.login_state.invalidate()
            await self.ensure_login()
            await self.page.goto(publish_url, wait_until="domcontentloaded")
            await self._best_effort_settle(30_000)

        await self.audit.screenshot(self.page, "publish_page_opened")
        await self.audit.dom_snapshot(self.page, "publish_page_opened")
        await self.select_image_tab()

    async def fill_note(self, content: XhsContent) -> None:
        """填充笔记内容到发布表单

        步骤:
        1. 上传图片（如有）—— 通过 file input 设置本地路径
        2. 填充标题 —— 使用 fill_first 匹配多个候选输入框
        3. 填充正文 —— 包含话题标签（#话题# 格式由 XhsContent 预处理）
        4. 按 ESC 关闭可能的弹窗（话题选择弹窗、自动补全等）
        5. 验证填充是否成功（防止静默填充失败）

        为什么填充后要按 ESC？
        填充正文时如果包含话题标签，小红书会弹出话题选择浮层，
        ESC 可以关闭这些浮层，确保后续操作不被遮挡。
        """
        self.audit.event("fill_note_start", image_count=len(content.images))
        if content.images:
            await self.upload_images(content.images)
        await fill_first(self.page, self.selectors["title_input_any"], content.title)
        self.audit.event("title_filled", length=len(content.title))
        await fill_first(self.page, self.selectors["body_input_any"], content.body_with_topics)
        self.audit.event("body_filled", length=len(content.body_with_topics), topic_count=len(content.topics))
        await self.page.keyboard.press("Escape")
        await self.page.wait_for_timeout(1200)
        await self.verify_filled(content)

    async def upload_images(self, images: list[Path]) -> None:
        """上传图片到发布页面

        找到文件输入框，设置本地文件路径，等待上传完成
        """
        self.audit.event("upload_images_start", images=[str(image) for image in images])
        input_locator = await self.find_image_upload_input()
        await input_locator.set_input_files([str(path) for path in images])
        await self.page.wait_for_timeout(5000)
        await self.audit.screenshot(self.page, "after_image_upload")
        self.audit.event("upload_images_done", count=len(images))

    async def select_image_tab(self) -> None:
        """切换到图文上传标签

        双策略降级:
        1. Playwright 内置的 get_by_text 精确匹配"上传图文"文本
        2. 失败则通过 JS 遍历 DOM 树查找并点击

        为什么需要这个步骤？
        小红书发布页默认可能停留在"上传视频"标签，
        需要手动切换到"上传图文"标签才能正确显示图片上传的 file input。
        """
        tab_text = "上传图文"
        clicked = False
        try:
            locator = self.page.get_by_text(tab_text, exact=True).first
            await locator.wait_for(state="visible", timeout=5000)
            await locator.click(force=True)
            clicked = True
        except Exception as exc:  # noqa: BLE001
            self.audit.event("image_tab_direct_click_failed", error=str(exc))

        if not clicked:
            try:
                clicked = bool(
                    await self.page.evaluate(
                        """
                        text => {
                          const candidates = Array.from(document.querySelectorAll('span, div, button, a'));
                          const el = candidates.find(node => (node.innerText || node.textContent || '').trim() === text);
                          if (!el) return false;
                          el.scrollIntoView({block: 'center', inline: 'center'});
                          el.click();
                          return true;
                        }
                        """,
                        tab_text,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self.audit.event("image_tab_js_click_failed", error=str(exc))

        self.audit.event("image_tab_selected" if clicked else "image_tab_select_failed")
        await self.page.wait_for_timeout(1800)
        await self.audit.screenshot(self.page, "image_tab_selected")
        await self.audit.dom_snapshot(self.page, "image_tab_selected")

    async def find_image_upload_input(self) -> Any:
        """查找图片上传输入框

        双策略降级:
        1. 使用配置文件中的选择器列表（selectors["image_upload_input_any"]）
        2. 失败则通过 JS 查找 accept 属性包含图片类型的 file input

        为什么策略 2 要检查 accept 属性？
        发布页可能有多个 file input（图片、视频、附件等），
        通过 accept 属性区分可以精确定位到图片上传的输入框。
        """
        try:
            return await first_attached(self.page, self.selectors["image_upload_input_any"], timeout_ms=5000)
        except Exception:
            pass

        # JS方式查找: 过滤accept属性包含图片类型的input
        handle = await self.page.evaluate_handle(
            """
            () => {
              const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
              return inputs.find(input => {
                const accept = (input.getAttribute('accept') || '').toLowerCase();
                return accept.includes('image') || accept.includes('.jpg') || accept.includes('.jpeg') || accept.includes('.png') || accept.includes('.webp');
              }) || null;
            }
            """
        )
        element = handle.as_element()
        if element:
            return element
        raise RuntimeError("image upload input was not found; page may still be on the video upload tab")

    async def verify_filled(self, content: XhsContent) -> None:
        """验证内容是否成功填充到表单

        检查策略:
        - 只检查标题前 20 个字符和正文前 20 个字符（足够确认填充成功，避免长文本匹配失败）
        - 使用 _page_contains 三层检测（可见元素 → input 值 → HTML 源码）

        为什么需要验证？
        Playwright 的 fill 操作可能静默失败（如元素被覆盖、Shadow DOM 遮挡等），
        不验证的话会带着空内容点发布，浪费一次发布机会。
        """
        title_ok = await self._page_contains(content.title[:20])
        body_probe = content.body[:20] if len(content.body) >= 20 else content.body
        body_ok = await self._page_contains(body_probe)
        self.audit.event("verify_filled", title_ok=title_ok, body_ok=body_ok)
        if not title_ok:
            raise RuntimeError("title fill verification failed")
        if body_probe and not body_ok:
            raise RuntimeError("body fill verification failed")

    async def click_publish_and_wait(self) -> dict[str, Any]:
        """点击发布按钮并等待结果

        发布成功检测的两种信号:
        1. URL 参数: published=true 出现在跳转后的 URL 中
        2. 页面元素: 通过 selectors["success_any"] 定位成功提示元素

        为什么需要两种检测方式？
        小红书不同版本的发布成功表现不同：有的版本 URL 带参数跳转，
        有的版本在当前页显示成功 toast。双信号覆盖两种情况。

        返回:
            status 为 "published"（确认成功）或 "publish_clicked_unconfirmed"（点击了但未确认）
        """
        self.audit.event("click_publish")
        await self.click_bottom_publish_button()
        await self.page.wait_for_timeout(2000)
        await self.confirm_publish_if_needed()
        await self.page.wait_for_timeout(8000)
        await self.audit.screenshot(self.page, "after_publish_click")
        await self.audit.dom_snapshot(self.page, "after_publish_click")

        # 检测发布成功的两种信号: URL参数或页面元素
        url_success = "published=true" in self.page.url
        success = url_success or await any_visible(self.page, self.selectors.get("success_any", []), timeout_ms=5_000)
        status = "published" if success else "publish_clicked_unconfirmed"
        return {
            "status": status,
            "mode": "publish",
            "url": self.page.url,
            "success_signal_found": success,
            "url_success_signal": url_success,
        }

    async def click_bottom_publish_button(self) -> None:
        """点击底部发布按钮（4 层降级策略）

        这是整个发布流程中最脆弱的环节，因为小红书使用自定义 Web Component
        <xhs-publish-btn>，其内部结构可能随版本变化。4 层降级确保最大兼容性::

            策略1: click_publish_component_button()
                尝试直接调用组件的 _onPublish 私有方法，或遍历 Shadow DOM 找到
                内部按钮元素并通过 pointer/mouse 事件模拟点击。
                最精确但最依赖内部实现。

            策略2: click_visible_publish_button()
                不依赖特定组件，全局搜索文本为"发布"的可见按钮，
                通过评分系统（红色优先、位置偏下、尺寸合理）选出最佳候选。

            策略3: 坐标点击
                定位 <xhs-publish-btn> 组件的 bounding box，
                按比例偏移点击右侧区域（发布按钮在组件右侧的红色药丸按钮）。
                纯几何定位，完全不依赖 DOM 结构。

            策略4: JS DOM 遍历
                全局遍历所有 button/div/span，找文本为"发布"且位于页面
                下半部分的元素，点击最底部的一个。

        为什么需要如此复杂的降级？
        小红书前端团队经常更新发布页的 Web Component 实现。
        单一策略可能在某次更新后全部失效，4 层降级确保至少有一种方式能工作。
        """
        if await self.click_publish_component_button():
            return

        if await self.click_visible_publish_button():
            return

        # 策略3: 通过坐标点击自定义组件
        custom = self.page.locator("xhs-publish-btn[is-publish='true']").first
        try:
            await custom.wait_for(state="attached", timeout=5000)
            await custom.scroll_into_view_if_needed(timeout=5000)
            await self.page.wait_for_timeout(300)
            box = await custom.bounding_box()
            if box:
                # The component renders two bottom buttons. The submit button is the right-side red pill.
                viewport = self.page.viewport_size or {"width": 1440, "height": 1000}
                x = min(box["x"] + box["width"] * 0.31, viewport["width"] - 20)
                y = min(box["y"] + box["height"] * 0.5, viewport["height"] - 20)
                self.audit.event("publish_button_mouse_click", x=x, y=y, box=box)
                await self.page.mouse.click(x, y)
                return
        except Exception as exc:  # noqa: BLE001
            self.audit.event("publish_button_component_click_failed", error=str(exc))

        # 策略4: JS遍历DOM查找文本为"发布"的按钮
        clicked = bool(
            await self.page.evaluate(
                """
                () => {
                  const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
                  const candidates = nodes
                    .filter(node => (node.innerText || node.textContent || '').trim() === '发布')
                    .map(node => {
                      const rect = node.getBoundingClientRect();
                      return {node, rect, visible: rect.width > 0 && rect.height > 0};
                    })
                    .filter(item => item.visible && item.rect.top > window.innerHeight * 0.55)
                    .sort((a, b) => b.rect.top - a.rect.top);
                  const item = candidates[0];
                  if (!item) return false;
                  item.node.click();
                  return true;
                }
                """
            )
        )
        if not clicked:
            raise RuntimeError("bottom publish button was not found")

    async def click_publish_component_button(self) -> bool:
        """策略1: 精确打击 - 在 <xhs-publish-btn> 组件内部定位发布按钮

        两层子策略:
        A. 直接调用组件暴露的 _onPublish() 私有方法（如果存在）
           这是最理想的方式，直接触发组件内部逻辑，无需定位 DOM 元素。
        B. 遍历 Shadow DOM + Light DOM 全部元素，通过多维评分定位按钮::

              评分维度:
              - textScore (0-8): 文本完全匹配 "发布" 得 8 分，包含得 4 分
              - roleScore (0-3): 标签为 button 或 role="button" 得 3 分
              - sizeScore (0-2): 尺寸在 40-180px × 24-70px 范围内得 2 分
              - colorScore (0+): 红色系（R>180, G<130, B<150）得 3 分，R>G+50 得 1 分

              过滤阈值: score >= 6 才作为候选，避免误点无关元素

        为什么使用 pointerdown/mousedown/pointerup/mouseup/click 事件序列？
        某些前端框架（如 Vue/React）会在 mousedown 或 pointerdown 上绑定事件处理，
        单纯的 click 可能无法触发。完整事件序列模拟真实用户操作。
        """
        result = await self.page.evaluate(
            """
            async () => {
              const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
              const host = document.querySelector("xhs-publish-btn[is-publish='true'][submit-disabled='false']");
              if (!host) return {clicked: false, reason: 'publish component not found'};

              host.scrollIntoView({block: 'center', inline: 'center'});
              await sleep(300);

              const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
              const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
              const submitText = host.getAttribute('submit-text') || '发布';
              const hostRect = host.getBoundingClientRect();

              // 策略1: 调用组件的私有_onPublish方法
              if (typeof host._onPublish === 'function') {
                const maybePromise = host._onPublish();
                if (maybePromise && typeof maybePromise.then === 'function') {
                  await maybePromise;
                }
                return {
                  clicked: true,
                  strategy: 'component_private_onPublish',
                  ownKeys: Object.keys(host).slice(0, 40),
                  hostRect: {x: hostRect.x, y: hostRect.y, width: hostRect.width, height: hostRect.height},
                  submitText
                };
              }

              // 策略2: 遍历Shadow DOM查找按钮(包括Shadow DOM)
              const allDeep = root => {
                const seen = [];
                const walk = node => {
                  if (!node) return;
                  if (node.nodeType === Node.ELEMENT_NODE) {
                    seen.push(node);
                    if (node.shadowRoot) walk(node.shadowRoot);
                  }
                  const children = node.children || [];
                  for (const child of children) walk(child);
                };
                walk(root);
                return seen;
              };
              const isVisible = node => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                  rect.bottom > 0 && rect.right > 0 &&
                  rect.top < viewportHeight && rect.left < viewportWidth &&
                  style.display !== 'none' && style.visibility !== 'hidden' &&
                  style.pointerEvents !== 'none';
              };
              const colorScore = node => {
                const style = window.getComputedStyle(node);
                const colors = [style.backgroundColor, style.borderColor, style.color].join(' ');
                const nums = colors.match(/\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
                let score = 0;
                for (let i = 0; i + 2 < nums.length; i += 3) {
                  const r = nums[i], g = nums[i + 1], b = nums[i + 2];
                  if (r > 180 && g < 130 && b < 150) score += 3;
                  if (r > g + 50 && r > b + 50) score += 1;
                }
                return score;
              };

              const nodes = allDeep(host);
              const candidates = nodes
                .filter(node => isVisible(node))
                .map(node => {
                  const text = (node.innerText || node.textContent || '').trim();
                  const rect = node.getBoundingClientRect();
                  const role = node.getAttribute('role') || '';
                  const tag = node.tagName.toLowerCase();
                  const textScore = text === submitText ? 8 : text.includes(submitText) ? 4 : 0;
                  const roleScore = tag === 'button' || role === 'button' ? 3 : 0;
                  const sizeScore = rect.width >= 40 && rect.width <= 180 && rect.height >= 24 && rect.height <= 70 ? 2 : 0;
                  return {node, text, rect, score: textScore + roleScore + sizeScore + colorScore(node)};
                })
                .filter(item => item.score >= 6)
                .sort((a, b) => b.score - a.score);

              if (candidates.length) {
                const best = candidates[0];
                const x = Math.min(Math.max(best.rect.left + best.rect.width / 2, 4), viewportWidth - 4);
                const y = Math.min(Math.max(best.rect.top + best.rect.height / 2, 4), viewportHeight - 4);
                const target = document.elementFromPoint(x, y) || best.node;
                target.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, clientX: x, clientY: y}));
                target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: x, clientY: y}));
                target.dispatchEvent(new PointerEvent('pointerup', {bubbles: true, clientX: x, clientY: y}));
                target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: x, clientY: y}));
                target.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: x, clientY: y}));
                return {
                  clicked: true,
                  strategy: 'shadow_or_light_dom_candidate',
                  x,
                  y,
                  text: best.text,
                  tag: best.node.tagName,
                  score: best.score,
                  hostRect: {x: hostRect.x, y: hostRect.y, width: hostRect.width, height: hostRect.height}
                };
              }

              return {
                clicked: false,
                reason: 'no deep candidate',
                shadowRoot: Boolean(host.shadowRoot),
                ownKeys: Object.keys(host).slice(0, 40),
                protoKeys: Object.getOwnPropertyNames(Object.getPrototypeOf(host)).slice(0, 80),
                hostRect: {x: hostRect.x, y: hostRect.y, width: hostRect.width, height: hostRect.height},
                scrollY: window.scrollY,
                submitText
              };
            }
            """
        )
        self.audit.event("publish_component_click", **result)
        return bool(result.get("clicked"))

    async def click_visible_publish_button(self) -> bool:
        """策略2: 全局搜索 - 通过评分系统定位最可能的"发布"按钮

        不依赖特定 Web Component，在整个页面范围内搜索文本为"发布"的元素。
        对每个候选元素向上遍历 5 层父元素，找到最合适的可点击容器::

            评分维度:
            - redScore:  红色系颜色得分（小红书发布按钮通常是红色）
            - 位置分:   位于页面下半部分（55% 以下）得 4 分
            - 宽度分:   50-180px 得 2 分
            - 高度分:   28-70px 得 2 分

            排除条件: disabled、aria-disabled、class 含 "disabled"

        为什么向上遍历父元素？
        文本"发布"可能在一个 <span> 内，但实际可点击的区域是其父 <button>。
        向上查找确保点击的是完整的按钮元素而非内联文本节点。
        """
        result = await self.page.evaluate(
            """
            () => {
              const publishText = '发布';
              const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
              const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
              const visibleRect = node => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                const visible = rect.width > 0 && rect.height > 0 &&
                  rect.bottom > 0 && rect.right > 0 &&
                  rect.top < viewportHeight && rect.left < viewportWidth &&
                  style.visibility !== 'hidden' && style.display !== 'none' &&
                  style.pointerEvents !== 'none';
                return visible ? rect : null;
              };
              const redScore = node => {
                const style = window.getComputedStyle(node);
                const colors = [style.backgroundColor, style.borderColor, style.color].join(' ');
                const nums = colors.match(/\\d+(?:\\.\\d+)?/g)?.map(Number) || [];
                let score = 0;
                for (let i = 0; i + 2 < nums.length; i += 3) {
                  const r = nums[i], g = nums[i + 1], b = nums[i + 2];
                  if (r > 180 && g < 120 && b < 140) score += 3;
                  if (r > g + 50 && r > b + 50) score += 1;
                }
                return score;
              };
              const textNodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'))
                .filter(node => (node.innerText || node.textContent || '').trim() === publishText);
              const candidates = [];
              for (const node of textNodes) {
                let current = node;
                for (let depth = 0; current && depth < 5; depth += 1, current = current.parentElement) {
                  const rect = visibleRect(current);
                  if (!rect) continue;
                  const disabled = current.disabled || current.getAttribute('aria-disabled') === 'true' ||
                    current.className?.toString().includes('disabled');
                  if (disabled) continue;
                  candidates.push({
                    node: current,
                    rect,
                    score: redScore(current) + (rect.top > viewportHeight * 0.55 ? 4 : 0) +
                      (rect.width >= 50 && rect.width <= 180 ? 2 : 0) +
                      (rect.height >= 28 && rect.height <= 70 ? 2 : 0)
                  });
                }
              }
              candidates.sort((a, b) => b.score - a.score || b.rect.top - a.rect.top);
              const best = candidates[0];
              if (!best) return {clicked: false, reason: 'no visible publish candidate'};
              best.node.scrollIntoView({block: 'center', inline: 'center'});
              const rect = best.node.getBoundingClientRect();
              const x = Math.min(Math.max(rect.left + rect.width / 2, 4), viewportWidth - 4);
              const y = Math.min(Math.max(rect.top + rect.height / 2, 4), viewportHeight - 4);
              const target = document.elementFromPoint(x, y) || best.node;
              target.dispatchEvent(new MouseEvent('mouseover', {bubbles: true, clientX: x, clientY: y}));
              target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, clientX: x, clientY: y}));
              target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, clientX: x, clientY: y}));
              target.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: x, clientY: y}));
              return {
                clicked: true,
                x,
                y,
                text: best.node.innerText || best.node.textContent || '',
                tag: best.node.tagName,
                className: String(best.node.className || ''),
                score: best.score,
                rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
              };
            }
            """
        )
        self.audit.event("publish_button_dom_click", **result)
        return bool(result.get("clicked"))

    async def confirm_publish_if_needed(self) -> None:
        """如存在确认弹窗则点击确认

        检测逻辑:
        1. 搜索文本为"确定"/"确认"/"继续发布"/"发布"的可见元素
        2. 过滤：只点击位于模态框（dialog/modal）内的按钮
        3. 优先选择模态框内的按钮（通过 closest() 检测父级 dialog 容器）

        为什么限制在模态框内？
        页面上可能存在多个文本为"发布"的按钮（如顶部导航栏的"发布"入口），
        只有弹窗内的"确认"按钮才是我们要点的。通过 closest() 检查确保不会误点。
        """
        result = await self.page.evaluate(
            """
            () => {
              const labels = new Set(['确定', '确认', '继续发布', '发布']);
              const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
              const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
              const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span'));
              const candidates = nodes.map(node => {
                const text = (node.innerText || node.textContent || '').trim();
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return {node, text, rect, style};
              }).filter(item =>
                labels.has(item.text) &&
                item.rect.width > 0 && item.rect.height > 0 &&
                item.rect.top >= 0 && item.rect.left >= 0 &&
                item.rect.top < viewportHeight && item.rect.left < viewportWidth &&
                item.style.display !== 'none' && item.style.visibility !== 'hidden'
              ).sort((a, b) => {
                const aModal = a.node.closest('[role="dialog"], .d-modal, .el-dialog, [class*="modal"], [class*="dialog"]') ? 1 : 0;
                const bModal = b.node.closest('[role="dialog"], .d-modal, .el-dialog, [class*="modal"], [class*="dialog"]') ? 1 : 0;
                return bModal - aModal || b.rect.top - a.rect.top;
              });
              const best = candidates[0];
              if (!best || !best.node.closest('[role="dialog"], .d-modal, .el-dialog, [class*="modal"], [class*="dialog"]')) {
                return {clicked: false};
              }
              const x = best.rect.left + best.rect.width / 2;
              const y = best.rect.top + best.rect.height / 2;
              best.node.click();
              return {clicked: true, text: best.text, x, y};
            }
            """
        )
        self.audit.event("confirm_publish_if_needed", **result)

    async def _looks_logged_in(self) -> bool:
        """检测当前页面是否处于登录状态

        通过 selectors["login_success_any"] 中的候选选择器查找登录标志元素。
        使用 any_visible（locator_utils 提供）依次尝试多个选择器，
        只要有一个可见就判定为已登录。

        为什么用"看起来已登录"这种模糊判断？
        小红书的登录态没有统一的检测接口，只能通过页面元素推测。
        常见的登录标志包括：用户头像、昵称显示、创作中心入口等。
        """
        try:
            return await any_visible(self.page, self.selectors["login_success_any"], timeout_ms=1800)
        except Exception:
            return False

    async def _best_effort_settle(self, timeout_ms: int) -> None:
        """等待页面加载稳定（最小侵入式等待）

        先尝试等待 networkidle（网络空闲），如果超时则不报错，
        降级为固定 1200ms 等待。

        为什么不直接用 networkidle？
        小红书页面可能有长连接（WebSocket、埋点上报、实时推送等），
        这些请求会导致 networkidle 永远不触发。
        "尽力等待，等不到就算了"的策略比硬性超时报错更实用。
        """
        try:
            # 单页应用（SPA）路由跳转后，动态内容通过 API 填充
            # 过早执行 click() 可能找不到目标元素，因为数据还未渲染。
            await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - modern apps may keep long-lived requests open.
            self.audit.event("networkidle_timeout_ignored", timeout_ms=timeout_ms, error=str(exc))
            await self.page.wait_for_timeout(1200)

    async def _page_contains(self, text: str) -> bool:
        """检测页面是否包含指定文本（三层降级检测）

        检测链::

            层1: Playwright get_by_text
                查找页面中可见的文本元素，最快最准确。
                但只能检测渲染后的可见文本，无法检测 input/textarea 的值。

            层2: JS 查询 input/textarea/contenteditable
                遍历所有表单元素和可编辑区域，检查 value/innerText。
                补充层1 无法覆盖的表单值检测。

            层3: HTML 源码匹配
                最终兜底，在完整 HTML 中搜索文本。
                最慢但最全面，能匹配到隐藏元素和注释中的文本。

        为什么需要三层？
        小红书发布页的输入框可能是 contenteditable 的 div（富文本编辑器），
        也可能是原生 textarea，还可能在 Shadow DOM 中。
        三层检测确保不遗漏任何存储位置。
        """
        if not text:
            return True
        try:
            await self.page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=1500)
            return True
        except Exception:
            pass
        try:
            return bool(
                await self.page.evaluate(
                    """
                    needle => {
                      const nodes = Array.from(document.querySelectorAll('input, textarea, [contenteditable="true"]'));
                      return nodes.some(node => {
                        const value = node.value || node.innerText || node.textContent || '';
                        return value.includes(needle);
                      }) || document.body.innerText.includes(needle);
                    }
                    """,
                    text,
                )
            )
        except Exception:
            try:
                html = await self.page.content()
            except Exception:
                return False
            return text in html


def load_json(path: Path) -> dict[str, Any]:
    """加载JSON文件并验证根对象类型"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload
