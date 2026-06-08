# 支持类型注解中的未来语法
from __future__ import annotations

from typing import Any


class LocatorNotFound(RuntimeError):
    """定位器未找到异常，当所有候选选择器都失败时抛出"""
    pass


async def first_visible(page: Any, specs: list[dict[str, Any]], *, timeout_ms: int = 1500) -> Any:
    """在多个候选选择器中找到第一个可见的元素并返回"""
    # 收集所有候选选择器的错误信息
    errors: list[str] = []
    for spec in specs:
        try:
            # 根据规范构建定位器
            locator = build_locator(page, spec)
            # 等待元素可见
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
            return locator.first
        except Exception as exc:  # noqa: BLE001 - 继续尝试其他候选选择器
            errors.append(f"{spec}: {exc}")
    # 所有选择器都失败，抛出异常
    raise LocatorNotFound("; ".join(errors))


async def first_attached(page: Any, specs: list[dict[str, Any]], *, timeout_ms: int = 1500) -> Any:
    """在多个候选选择器中找到第一个已附加（attached）到DOM的元素并返回"""
    # 收集所有候选选择器的错误信息
    errors: list[str] = []
    for spec in specs:
        try:
            # 根据规范构建定位器
            locator = build_locator(page, spec)
            # 等待元素附加到DOM（不必可见）
            await locator.first.wait_for(state="attached", timeout=timeout_ms)
            return locator.first
        except Exception as exc:  # noqa: BLE001 - 继续尝试其他候选选择器
            errors.append(f"{spec}: {exc}")
    # 所有选择器都失败，抛出异常
    raise LocatorNotFound("; ".join(errors))


async def any_visible(page: Any, specs: list[dict[str, Any]], *, timeout_ms: int = 1000) -> bool:
    """检查候选选择器中是否有任意一个可见的元素"""
    try:
        # 尝试找到第一个可见元素
        await first_visible(page, specs, timeout_ms=timeout_ms)
        return True
    except LocatorNotFound:
        # 未找到可见元素
        return False


def build_locator(page: Any, spec: dict[str, Any]) -> Any:
    """根据选择器规范构建对应的 Playwright 定位器"""
    # 获取选择器类型和值
    kind = spec.get("kind")
    value = spec.get("value")
    if kind == "text":
        # 按文本内容查找（不要求精确匹配）
        return page.get_by_text(str(value), exact=False)
    if kind == "placeholder":
        # 按占位符文本查找
        return page.get_by_placeholder(str(value), exact=False)
    if kind == "role":
        # 按 ARIA 角色查找
        role = spec.get("role")
        name = spec.get("name")
        return page.get_by_role(str(role), name=str(name) if name else None)
    if kind == "css":
        # 按 CSS 选择器查找
        return page.locator(str(value))
    raise ValueError(f"unsupported selector kind: {kind}")


async def fill_first(page: Any, specs: list[dict[str, Any]], value: str, *, timeout_ms: int = 2500) -> None:
    """在第一个可见元素中填充值，如果失败则通过键盘输入"""
    # 找到第一个可见元素
    locator = await first_visible(page, specs, timeout_ms=timeout_ms)
    try:
        # 直接填充值
        await locator.fill(value)
    except Exception:
        # 如果填充失败，手动清除后重新输入
        await locator.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.type(value)


async def click_first(page: Any, specs: list[dict[str, Any]], *, timeout_ms: int = 2500) -> None:
    """点击第一个可见元素"""
    # 找到第一个可见元素并点击
    locator = await first_visible(page, specs, timeout_ms=timeout_ms)
    await locator.click()
