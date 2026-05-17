# astrbot_plugin_meme_generate

一个 AstrBot 插件：
- 保存群聊图片到本地表情包库
- 手动导入图片 URL
- 基于聊天文本氛围（关键词）匹配并发送表情包

## 指令

- `/meme_help` 查看帮助
- `/meme_save [mood]` 保存当前消息中的图片（可选指定 mood）
- `/meme_import [mood] [url]` 手动导入图片
- `/meme_list` 查看库存摘要
- `/meme_send [文本]` 根据文本情绪选图并发送

## 说明

- 图片和索引保存在 `data/meme_generate/` 下。
- 当前版本使用关键词匹配情绪（happy/angry/sad/surprised/awkward）。
- 发送图片接口会尝试兼容不同 AstrBot 适配器（`image_result` / `file_result`）。
