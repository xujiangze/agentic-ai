# 龙虾通知协议
<!-- 中文注释：定义 xhs-auto-publisher 与龙虾（通知系统）之间的数据交换协议，包括二维码登录通知的处理方式 -->

这份文档定义的是：

- `xhs-auto-publisher` 如何把登录二维码交给龙虾
- 龙虾应该如何读取 payload 并转发到飞书群

## 1. 文件位置
<!-- 中文注释：定义 payload 文件的生成位置和命名规则 -->

当任务需要扫码登录时，项目会生成：

```text
runtime/lobster-notify/<run_id>/login_qr.payload.json
```

例如：

```text
runtime/lobster-notify/20260516-153000/login_qr.payload.json
```

## 2. payload 示例
<!-- 中文注释：完整的 JSON payload 结构示例，包含时间戳、渠道、类型、标题、运行 ID、图片路径、消息内容和发送配置 -->

```json
{
  "ts": "2026-05-16T15:30:00+08:00",
  "channel": "lobster_channel",
  "kind": "login_qr",
  "platform": "xiaohongshu",
  "title": "[XHS Cloud Login] 小红书登录二维码",
  "run_id": "20260516-153000",
  "screenshot_path": "runtime/runs/20260516-153000/screenshots/login_qr.png",
  "message_lines": [
    "[XHS Cloud Login] 小红书登录二维码",
    "Run ID: 20260516-153000",
    "图片路径: runtime/runs/20260516-153000/screenshots/login_qr.png",
    "请把这张二维码图片直接发到飞书群，用户扫码后等待任务继续。"
  ],
  "action": "send_image_to_feishu_group",
  "delivery": {
    "type": "image_file",
    "path": "runtime/runs/20260516-153000/screenshots/login_qr.png",
    "caption_lines": [
      "[XHS Cloud Login] 小红书登录二维码",
      "Run ID: 20260516-153000",
      "图片路径: runtime/runs/20260516-153000/screenshots/login_qr.png",
      "请把这张二维码图片直接发到飞书群，用户扫码后等待任务继续。"
    ]
  }
}
```

## 3. 龙虾必须处理的字段
<!-- 中文注释：列出龙虾需要解析的关键字段，包括通知类型、运行 ID、交付方式、图片路径和说明文字 -->

最关键的是这几个：

- `kind`
  当前固定为 `login_qr`
- `run_id`
  当前任务 ID
- `delivery.type`
  当前固定为 `image_file`
- `delivery.path`
  要发出去的二维码图片路径
- `delivery.caption_lines`
  跟随图片一起发送的说明文字

## 4. 龙虾应该怎么做
<!-- 中文注释：定义龙虾处理 payload 的完整流程，包括 JSON 解析、类型判断、文件读取和飞书群消息发送 -->

读取到 payload 后，按这个顺序处理：

1. 解析 JSON
2. 判断 `kind == "login_qr"`
3. 读取 `delivery.path`
4. 找到本地二维码图片
5. 把图片直接发到飞书群
6. 把 `delivery.caption_lines` 拼成多行文本一并发送

## 5. 推荐发送效果
<!-- 中文注释：定义飞书群中应展示的消息格式，包括二维码图片、运行 ID 和简洁的操作提示 -->

飞书群里建议至少包含：

- 二维码图片
- Run ID
- 简短提示语

例如：

```text
[XHS Cloud Login] 小红书登录二维码
Run ID: 20260516-153000
请扫码完成登录，扫码后等待任务自动继续。
```

## 6. 不需要做的事
<!-- 中文注释：明确龙虾不需要执行的操作，避免不必要的复杂度，如生成公网链接、配置 nginx 等 -->

龙虾不需要：

- 生成公网访问链接
- 暴露 nginx 静态目录
- 反向代理二维码图片
- 修改图片内容

它只需要把图片发出去。

## 7. 失败处理建议
<!-- 中文注释：定义龙虾发送失败时的回报格式，包括 payload 路径、图片路径、失败原因和运行 ID，便于快速排查问题 -->

如果龙虾发送失败，建议至少回报：

- payload 文件路径
- 图片路径
- 失败原因
- 当前 run_id

这样排查会很快。
