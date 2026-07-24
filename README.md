[![HarukaBot](https://socialify.git.ci/SK-415/HarukaBot/image?description=1&font=Source%20Code%20Pro&forks=1&issues=1&language=1&logo=https%3A%2F%2Fraw.githubusercontent.com%2FSK-415%2FHarukaBot%2Fmaster%2Fdocs%2F.vuepress%2Fpublic%2Flogo.png&owner=1&pattern=Charlie%20Brown&stargazers=1&theme=Dark)](https://haruka-bot.sk415.icu/)

# [HarukaBot](https://haruka-bot.sk415.icu)——优雅的 B 站推送 QQ 机器人

名称来源：[@白神遥Haruka](https://space.bilibili.com/477332594)

Logo 画师：[@Ratto](https://space.bilibili.com/23242907)

[![VERSION](https://img.shields.io/pypi/v/haruka-bot)](https://haruka-bot.sk415.icu/about/CHANGELOG.html)
[![qq group](https://img.shields.io/badge/QQ%E7%BE%A4-629574472-orange)](https://jq.qq.com/?_wv=1027&k=sHPbCRAd)
[![time tracker](https://wakatime.com/badge/github/SK-415/HarukaBot.svg)](https://wakatime.com/badge/github/SK-415/HarukaBot)

## 简介

一款将哔哩哔哩 UP 主的直播与动态信息推送至 QQ 的机器人。基于 [NoneBot2](https://github.com/nonebot/nonebot2) 开发，前身为 [dd-bot](https://github.com/SK-415/dd-bot) 。

## 特色功能

HarukaBot 针对不同的推送场景（粉丝群、娱乐群、直播通知群），提供了个性化设置：

- 自定义推送内容，每位 UP 主可限制仅动态、仅直播。
- 群内开启权限限制，仅管理员以上可以使用机器人。
- 指定推送内容@全体成员，次数用光自动忽略。
- 同时连接多个 QQ 号，避免@全体成员次数不够。

## [文档（点击查看）](https://haruka-bot.sk415.icu)

## 部分功能展示

![demo](/docs/.vuepress/public/demo.png)

## 特别感谢

- [@mnixry](https://github.com/mnixry)：感谢混淆佬为本项目提供的**技♂术指导**。
- [@wosiwq](https://github.com/wosiwq)：感谢 W 桑撰写的「小小白白话文」。
- [NoneBot2](https://github.com/nonebot/nonebot2)：HarukaBot 使用的开发框架。
- [go-cqhttp](https://github.com/Mrs4s/go-cqhttp)：稳定完善的 CQHTTP 实现。
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)：非常详细的 B 站 API 文档。
- [bilibili_api](https://github.com/Passkou/bilibili_api)：Python 实现的 B 站 API 库。
- [HarukaBot_Guild_Patch](https://github.com/17TheWord/HarukaBot_Guild_Patch)：可以让HarukaBot适用于频道的补丁。（已合入 HarukaBot）

## 支持与贡献

觉得好用可以给这个项目点个 Star 或者去 [爱发电](https://afdian.net/@HarukaBot) 投喂我。

有意见或者建议也欢迎提交 [Issues](https://github.com/SK-415/HarukaBot/issues) 和 [Pull requests](https://github.com/SK-415/HarukaBot/pulls)。

## 许可证
本项目使用 [GNU AGPLv3](https://choosealicense.com/licenses/agpl-3.0/) 作为开源许可证。


## websocket-client
ws://127.0.0.1:7070/onebot/v11/ws

## 群聊 B 站视频转发

配置允许使用该功能的 QQ 群后，机器人会捕捉群消息中的
`bilibili.com/video/...`、`b23.tv/...`、`b23.wtf/...` 和
`bili2233.cn/...` 视频链接，下载最高不超过配置清晰度的 DASH 视频、音频流，
通过 FFmpeg 合并为 MP4，再以合并转发消息发送标题信息和视频。

```dotenv
# JSON 数组，或使用逗号/空格分隔的群号
HARUKA_BILI_VIDEO_GROUPS=[123456789,987654321]
# 可选；登录 Cookie 能获取账号有权观看的清晰度，请妥善保管
HARUKA_BILI_VIDEO_COOKIE=
HARUKA_BILI_VIDEO_QUALITY=80
HARUKA_BILI_VIDEO_MAX_SIZE_MB=100
HARUKA_BILI_VIDEO_MAX_LINKS=3
HARUKA_BILI_VIDEO_CONCURRENCY=2
HARUKA_BILI_VIDEO_TIMEOUT=600
```

宿主机运行时需安装 `ffmpeg` 并确保它在 `PATH` 中；Docker 镜像已内置。
视频使用 `file://` 地址交给 OneBot 实现上传，因此 OneBot 与 HarukaBot 分开
部署时，需要让两者共享 `HARUKA_DIR` 对应的目录及相同文件路径。

## Web 直播订阅管理

HarukaBot 内置了直播订阅管理页面，可直接通过直播间短号、真实房间号或
`live.bilibili.com` 链接添加监控，并选择通知群和推送机器人。

在环境配置中设置管理密码：

```dotenv
HARUKA_WEB_PASSWORD=请设置一个强密码
# 可选；留空时使用管理密码派生签名密钥
HARUKA_WEB_SECRET=
HARUKA_WEB_SESSION_TTL=43200
# 通过 HTTPS 反向代理访问时设为 True
HARUKA_WEB_COOKIE_SECURE=False
```

启动机器人后访问：

```text
http://<服务器地址>:7070/admin/
```

未设置 `HARUKA_WEB_PASSWORD` 时，管理 API 会返回 503，不会以无认证模式开放。
页面支持查看、搜索、新增、编辑和删除订阅。在线 OneBot 机器人会提供群列表；
机器人离线时仍可手动填写机器人 QQ 和通知群号。

### 导出

管理页可以下载 CSV 和 JSON 两种全量导出。导出按“一个订阅关系一行/一项”
记录数据，因此同一直播间对应多个群或机器人时会保留多条记录。字段包含主播
UID、名称、直播间号、直播状态、通知目标类型与 ID、频道信息、机器人 QQ 与
在线状态、直播/动态/@全体开关及直播时长。

CSV 使用带 BOM 的 UTF-8 编码，可直接使用 Excel 打开；JSON 包含导出时间和
完整的 `subscriptions` 数组。
