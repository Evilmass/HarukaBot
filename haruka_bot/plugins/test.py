from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.event import GroupMessageEvent

from ..utils import on_command, permission_check, to_me

# 要在 plugins/__init__.py 导入模块
test_handler = on_command(
    cmd="test_handler",
    aliases={"test", "测试"},
    rule=to_me(),
    priority=5,
)
# test_handler.__doc__ = """测试"""

test_handler.handle()(permission_check)


# @test_handler.handle()
async def get_test_handler(event: GroupMessageEvent):
    # message_list = await db.get_test_handler()
    # for ml in message_list:
    #     if ml["group_id"] == await get_type_id(event):
    #         await test_handler.finish(ml["message"])
    #         break

    message = Message(
        [
            # MessageSegment(type="markdown", data={"markup": "**markup**"}),
            MessageSegment(type="text", data={"text": "world"}),
            Message.template("data {}").format(MessageSegment.text("{'hello': 'world'}")),
            Message.template("图片 {} 标签: {}；画师：{}").format(
                MessageSegment.image("https://koishi.chat/logo.png", cache=True, timeout=5),
                "无",
                "无",
            ),
        ]
    )

    # await test_handler.send(message)
    await test_handler.finish(message)
