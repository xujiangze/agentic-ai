# 小红书自动发布器核心模块
# 提供登录管理、内容发布、图文上传等自动化功能

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
    """小红书发布器主类 - 管理登录、内容填充和发布流程"""
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

        流程: 登录 → 打开发布页 → 填充内容 → 发布/草稿

        参数:
            content: 待发布的内容对象

        返回:
            包含状态、URL等信息的字典
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

        策略:
        1. 检查登录状态缓存
        2. 如果缓存失效，访问主页检测登录状态
        3. 如未登录，准备二维码并等待用户扫码
        """
        home_url = str(self.app_config["creator_home_url"])
        # 缓存命中，无需重新登录
        if self.login_state.is_valid():
            self.audit.event("login_cache_hit")
            return

        # 缓存失效，访问主页检测
        self.audit.event("login_cache_miss", url=home_url)
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

        每秒检测一次登录状态，每30秒截图一次
        超时后抛出异常
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

        1. 尝试点击二维码切换按钮(多个候选选择器)
        2. 截取二维码图片
        3. 通过云通知推送二维码给用户
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

        1. 访问发布页URL
        2. 验证登录状态(如失效则重新登录)
        3. 切换到图文上传标签
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
        1. 上传图片(如有)
        2. 填充标题
        3. 填充正文(包含话题标签)
        4. 按ESC关闭可能的弹窗
        5. 验证填充是否成功
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

        策略:
        1. 尝试直接点击文本"上传图文"
        2. 失败则通过JS遍历DOM查找并点击
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

        策略:
        1. 使用配置的选择器查找
        2. 失败则通过JS查找accept属性包含image的file输入框
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
        """验证内容是否成功填充

        检查页面是否包含标题和正文的片段
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

        1. 点击底部发布按钮
        2. 如有确认弹窗则点击确认
        3. 等待并检测发布成功信号(URL或页面元素)
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
        """点击底部发布按钮(多策略)

        策略优先级:
        1. 尝试调用Web Component的_onPublish方法
        2. 查找可见的"发布"按钮并点击
        3. 通过坐标点击(xhs-publish-btn组件右侧按钮)
        4. 最后的JS遍历查找并点击
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
        """策略1: 尝试直接调用Web Component的_onPublish私有方法

        如果xhs-publish-btn组件暴露_onPublish方法，直接调用它
        否则遍历Shadow DOM查找符合条件的按钮元素
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
        """策略2: 查找并点击可见的"发布"按钮

        评分标准:
        - 红色按钮优先(小红书发布按钮通常是红色)
        - 位于页面下半部分
        - 合适的尺寸(宽50-180px, 高28-70px)
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

        查找模态框中的"确定"、"确认"等按钮并点击
        优先选择模态框内的按钮
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

        通过查找登录成功标志元素判断
        """
        try:
            return await any_visible(self.page, self.selectors["login_success_any"], timeout_ms=1800)
        except Exception:
            return False

    async def _best_effort_settle(self, timeout_ms: int) -> None:
        """等待页面加载稳定

        尝试等待网络空闲，超时则忽略(现代应用可能有长连接)
        """
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception as exc:  # noqa: BLE001 - modern apps may keep long-lived requests open.
            self.audit.event("networkidle_timeout_ignored", timeout_ms=timeout_ms, error=str(exc))
            await self.page.wait_for_timeout(1200)

    async def _page_contains(self, text: str) -> bool:
        """检测页面是否包含指定文本

        策略:
        1. 使用Playwright的get_by_text查找可见元素
        2. 失败则通过JS查找input/textarea/可编辑区域的值
        3. 最后回退到HTML源码查找
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
