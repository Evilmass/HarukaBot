from nonebot.adapters.onebot.v11.event import Event, GroupMessageEvent
from nonebot.matcher import matchers

from ..features import ALL_FEATURES
from ..utils import group_features, on_command, permission_check, to_me
from ..version import __version__

help = on_command("帮助", rule=to_me(), priority=5, feature="help")

help.handle()(permission_check)


@help.handle()
async def _(event: Event):
    available = (
        group_features(event.group_id)
        if isinstance(event, GroupMessageEvent)
        else ALL_FEATURES
    )
    message = "HarukaBot目前支持的功能：\n（请将UID替换为需要操作的B站UID）\n"
    for matchers_list in matchers.values():
        for matcher in matchers_list:
            if matcher.plugin_name and matcher.plugin_name.startswith("haruka_bot") and matcher.__doc__:
                feature = getattr(matcher, "haruka_feature", None)
                if feature is not None and feature not in available:
                    continue
                message += matcher.__doc__ + "\n"
    # message += f"\n当前版本：v{__version__}\n" "详细帮助：https://haruka-bot.sk415.icu/usage/"
    await help.finish(message)
