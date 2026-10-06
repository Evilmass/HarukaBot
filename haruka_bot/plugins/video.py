import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.rule import Rule

from ..video.files import VIDEO_SERVE_PREFIX
from ..video.service import allowed_platforms, video_service


async def _enabled_group(event: GroupMessageEvent) -> bool:
    return bool(allowed_platforms(event.group_id))


video = on_message(rule=Rule(_enabled_group), priority=20, block=False)


@video.handle()
async def handle_video(bot: Bot, event: GroupMessageEvent):
    await video_service.handle(bot, event)


app = nonebot.get_app()
if not getattr(app.state, "haruka_video_serve_registered", False):
    app.add_api_route(
        f"{VIDEO_SERVE_PREFIX}/{{task_id}}/video.mp4", video_service.store.serve,
        methods=["GET", "HEAD"], include_in_schema=False,
    )
    app.state.haruka_video_serve_registered = True
    driver = nonebot.get_driver()
    driver.on_startup(video_service.start)
    driver.on_shutdown(video_service.close)
