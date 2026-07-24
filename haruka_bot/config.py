import json
import re
from typing import List, Optional

from loguru import logger
from nonebot import get_driver
from pydantic import BaseSettings, validator
from pydantic.fields import ModelField


# 其他地方出现的类似 from .. import config，均是从 __init__.py 导入的 Config 实例
class Config(BaseSettings):
    fastapi_reload: bool = False
    haruka_dir: Optional[str] = None
    haruka_web_password: Optional[str] = None
    haruka_web_secret: Optional[str] = None
    haruka_web_session_ttl: int = 43200
    haruka_web_cookie_secure: bool = False
    haruka_to_me: bool = True
    haruka_live_off_notify: bool = False
    haruka_live_duration_day_start_hour: int = 0
    haruka_live_duration_top_n: int = 8
    haruka_proxy: Optional[str] = None
    haruka_interval: int = 10
    haruka_live_interval: int = haruka_interval
    haruka_dynamic_interval: int = 0
    haruka_dynamic_at: bool = False
    haruka_screenshot_style: str = "mobile"
    haruka_captcha_address: str = "https://captcha-cd.ngworks.cn"
    haruka_captcha_token: str = "harukabot"
    haruka_browser_ua: Optional[str] = None
    haruka_dynamic_timeout: int = 30
    haruka_dynamic_font_source: str = "system"
    haruka_dynamic_font: Optional[str] = "Noto Sans CJK SC"
    haruka_dynamic_big_image: bool = False
    haruka_command_prefix: str = ""
    haruka_bili_video_groups: List[int] = []
    haruka_bili_video_cookie: Optional[str] = None
    haruka_bili_video_quality: int = 80
    haruka_bili_video_max_size_mb: int = 100
    haruka_bili_video_max_links: int = 3
    haruka_bili_video_concurrency: int = 2
    haruka_bili_video_ffmpeg: str = "ffmpeg"
    haruka_bili_video_timeout: int = 600
    # 频道管理员身份组
    haruka_guild_admin_roles: List[str] = ["频道主", "超级管理员"]
    ignore_group: Optional[List[int]]

    @validator("haruka_bili_video_groups", pre=True)
    def parse_bili_video_groups(cls, v):
        """群号既支持 JSON 数组，也支持逗号或空格分隔。"""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            value = v.strip()
            if value.startswith("["):
                value = json.loads(value)
            else:
                value = [item for item in re.split(r"[\s,;]+", value) if item]
            v = value
        return [int(group_id) for group_id in v]

    @validator(
        "haruka_bili_video_quality",
        "haruka_bili_video_max_size_mb",
        "haruka_bili_video_max_links",
        "haruka_bili_video_concurrency",
        "haruka_bili_video_timeout",
    )
    def positive_bili_video_setting(cls, v: int):
        return max(v, 1)

    @validator("haruka_interval", "haruka_live_interval", "haruka_dynamic_interval")
    def non_negative(cls, v: int, field: ModelField):
        """定时器为负返回默认值"""
        return field.default if v < 1 else v

    @validator("haruka_web_session_ttl")
    def valid_web_session_ttl(cls, v: int):
        """管理会话有效期至少为一分钟。"""
        return max(v, 60)

    @validator("haruka_live_duration_day_start_hour")
    def valid_live_duration_day_start_hour(cls, v: int):
        """直播时长统计日的起始小时必须在 0 至 23 之间。"""
        if not 0 <= v <= 23:
            raise ValueError(
                "haruka_live_duration_day_start_hour must be between 0 and 23"
            )
        return v

    @validator("haruka_live_duration_top_n")
    def valid_live_duration_top_n(cls, v: int):
        """耐播王榜单至少展示一名主播。"""
        return max(v, 1)

    @validator("haruka_screenshot_style")
    def screenshot_style(cls, v: str):
        if v != "mobile":
            logger.warning("截图样式目前只支持 mobile，pc 样式现已被弃用")
        return "mobile"

    class Config:
        extra = "ignore"

        @classmethod
        def parse_env_var(cls, field_name: str, raw_value: str):
            if field_name == "haruka_bili_video_groups":
                return raw_value
            return json.loads(raw_value)


global_config = get_driver().config
plugin_config = Config.parse_obj(global_config)
