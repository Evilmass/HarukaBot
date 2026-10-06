import json

from nonebot.adapters.onebot.v11.event import GroupMessageEvent


def message_search_text(event: GroupMessageEvent) -> str:
    """同时检查纯文本、原始消息及卡片消息段数据。"""
    parts = [event.get_plaintext(), event.raw_message, str(event.message)]
    parts.extend(
        json.dumps(segment.data, ensure_ascii=False) for segment in event.message
    )
    return "\n".join(part for part in parts if part)


