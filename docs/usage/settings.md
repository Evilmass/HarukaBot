# 进阶配置

HarukaBot 存在一些**非必须的**进阶配置项，用户可以在 `.env.prod` 或 `.env.dev` 文件中添加这些配置来改变 HarukaBot 的**默认行为**。

::: tip 提示
添加配置项只需在 `.env.*` 文件最底下另起一行直接添加即可。
::: details 示例（点我展开）

```json {7-8}
HOST=0.0.0.0
PORT=8080
SUPERUSERS=[]
NICKNAME=[]
COMMAND_START=[""]
COMMAND_SEP=["."]
HARUKA_DIR="./data/"
HARUKA_TO_ME=false
HARUKA_GUILD_ADMIN_ROLES=["Haruka", "频道主"]
```

:::

## HARUKA_DIR

默认值：None

修改数据文件默认存储路径，默认存在 `haruka-bot` 包安装目录下。

**不推荐**使用默认存储位置，这会使得数据文件迁移与管理异常麻烦。**推荐**设置 `HARUKA_DIR="./data/"`，即当前目录的 `data` 文件夹下。

::: tip 提示
如果使用 `hb-cli` 部署，会**自动**在 `.env.prod` 中添加 `HARUKA_DIR="./data/"`。
:::

```json
HARUKA_DIR="./data/"
```

## HARUKA_WEB_PASSWORD

默认值：None

Web 直播订阅管理页面的登录密码。配置后可访问
`http://<服务器地址>:<PORT>/admin/`；未配置时管理 API 返回 503，不会以
无认证模式开放。请使用独立的强密码。

```yml
HARUKA_WEB_PASSWORD=请设置一个强密码
```

## HARUKA_WEB_SECRET

默认值：None

Web 会话签名密钥。留空时从 `HARUKA_WEB_PASSWORD` 派生；单独配置后，更换
管理密码不会自动使已有会话失效。

```yml
HARUKA_WEB_SECRET=请设置一段随机字符串
```

## HARUKA_WEB_SESSION_TTL

默认值：43200

Web 登录会话有效期，单位为秒，最小 60 秒。默认值为 12 小时。

```yml
HARUKA_WEB_SESSION_TTL=43200
```

## HARUKA_WEB_COOKIE_SECURE

默认值：False

是否仅允许浏览器通过 HTTPS 发送管理会话 Cookie。通过 HTTPS 反向代理对外
提供管理页面时应设置为 `True`；直接使用 HTTP 访问时必须保持 `False`，否则
浏览器无法保持登录状态。

```yml
HARUKA_WEB_COOKIE_SECURE=True
```

## HARUKA_TO_ME

默认值：True

在群里使用命令前是否需要 @机器人。设置为 `False` 则可以直接触发指令。

```json
HARUKA_TO_ME=False
```

## HARUKA_LIVE_OFF_NOTIFY

默认值：False

是否开启下播提醒。

```yml
HARUKA_LIVE_OFF_NOTIFY=True
```

## HARUKA_PROXY

默认值：None

设置后所有网络请求将使用代理端口，仅支持 HTTP 代理。

```yml
HARUKA_PROXY=http://127.0.0.1:10809
```

## HARUKA_INTERVAL

默认值：10

不推荐使用，请更换为 `HARUKA_LIVE_INTERVAL`。
直播刷新间隔，单位：秒。

```yml
HARUKA_INTERVAL=20
```

## HARUKA_DYNAMIC_ENABLED

默认值：True

动态推送全局开关。设置为 `False` 时不会启动动态爬取任务；修改后需要重启
HarukaBot。

```yml
HARUKA_DYNAMIC_ENABLED=False
```

## HARUKA_DYNAMIC_MAX_PUSH_PER_POLL

默认值：1

每轮爬取最多推送的动态数量。设置为 `1` 时只推送最新一条；设置为大于
`1` 的值时，最多补发最近的对应数量；设置为 `0` 时保留旧版的全部补发
行为。负数为无效配置。

```yml
HARUKA_DYNAMIC_MAX_PUSH_PER_POLL=1
```

## HARUKA_DYNAMIC_INTERVAL

默认值：0

动态刷新间隔，单位：秒。设置为 0 时根据网络情况自动调整间隔。

```yml
HARUKA_DYNAMIC_INTERVAL=5
```

## HARUKA_LIVE_INTERVAL

默认值：`HARUKA_INTERVAL` 设置的值

直播刷新间隔，单位：秒。

```yml
HARUKA_LIVE_INTERVAL=20
```

## HARUKA_LIVE_DURATION_DAY_START_HOUR

默认值：0

“耐播王”每日统计的切日小时，时区固定为 `Asia/Shanghai`。例如设置为
`4` 时，一个统计日从当天 04:00 持续到次日 04:00，完整榜单也会在
04:00 推送。

```yml
HARUKA_LIVE_DURATION_DAY_START_HOUR=4
```

## HARUKA_LIVE_DURATION_TOP_N

默认值：8

“耐播王”榜单最多展示的主播数量。

```yml
HARUKA_LIVE_DURATION_TOP_N=10
```

## HARUKA_DYNAMIC_AT

默认值：False

动态、投稿是否也要@全体。

```yml
HARUKA_DYNAMIC_AT=True
```

## HARUKA_BILI_VIDEO_PUBLIC_BASE_URL

默认值：未配置

HarukaBot 对 OneBot/NapCat 可访问的 HTTP 地址，用于临时传输下载并合并后的
B 站视频。NapCat 下载完成后，小于等于 95 MB 的文件作为普通群视频发送，
更大的文件自动上传为群文件。启用群聊 B 站视频转发时必须配置，不要填写
`0.0.0.0` 或 `127.0.0.1`，应填写 NapCat 实际能够访问的局域网地址或域名。

```yml
HARUKA_BILI_VIDEO_PUBLIC_BASE_URL=http://192.168.31.131:7070
```

<!-- ## HARUKA_SCREENSHOT_STYLE

默认值：mobile

截图样式，可选值：mobile（手机）、pc（电脑）。

```yml
HARUKA_SCREENSHOT_STYLE=pc
``` -->

## HARUKA_CAPTCHA_ADDRESS

默认值：<https://captcha-cd.ngworks.cn>

验证码地址，用于解决动态截图验证码问题。
（如果你不知道这是什么，请忽略）

```yml
HARUKA_CAPTCHA_ADDRESS=https://captcha-cd.ngworks.cn
```

## HARUKA_CAPTCHA_TOKEN

默认值：harukabot

验证码 Token，用于验证码服务器鉴权，若不填写一天内只能使用 5 次。

```yml
HARUKA_CAPTCHA_TOKEN=harukabot
```

## HARUKA_BROWSER_UA

默认值：""

自定义浏览器 UA
（如果你不知道这是什么，请忽略）

```yml
HARUKA_BROWSER_UA="Mozilla/5.0 (Linux; Android 10; Redmi K30 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.210 Mobile Safari/537.36"
```

## HARUKA_DYNAMIC_TIMEOUT

默认值：10

动态加载超时，单位秒。
网络不好一直超时请调大此数值。

```json
HARUKA_DYNAMIC_TIMEOUT=30
```

## HARUKA_DYNAMIC_FONT

默认值："Noto Sans CJK SC"

自定义动态截图使用的字体。只能使用系统中已经安装的字体。

```json
HARUKA_DYNAMIC_FONT="Microsoft YaHei"
```

## HARUKA_DYNAMIC_BIG_IMAGE

默认值：False

是否使用大图模式，大图模式下会将动态图片扩展至页宽。

```json
HARUKA_DYNAMIC_BIG_IMAGE=True
```

## HARUKA_COMMAND_PREFIX

默认值：""

添加命令前缀，所有 HarukaBot 的命令需要带上前缀才能触发。

```json
# 使用方式：“hb帮助”、“hb关注列表”
HARUKA_COMMAND_PREFIX="hb"
```

## HARUKA_GUILD_ADMIN_ROLES

默认值：["超级管理员", "频道主"]

在频道里使用命令的身份组，可以写入多个身份组

```json
HARUKA_GUILD_ADMIN_ROLES=["Haruka", "频道主"]
```
