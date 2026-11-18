"""群功能白名单的解析与匹配。"""

import re
from typing import Dict, FrozenSet, Iterable, List


FEATURE_NAMES = (
    "video",
    "save_sticker",
    "sub",
    "live",
    "dynamic",
    "at",
    "permission",
    "live_duration",
    "help",
    "server_ip",
    "maintenance",
)
ALL_FEATURES = frozenset(FEATURE_NAMES)
FEATURE_ALIASES = {
    "save": "save_sticker",
    "sticker": "save_sticker",
    "ip": "server_ip",
}
ALL_KEYWORD = "all"


def normalize_features(value) -> List[str]:
    """把配置值转成去重后的功能列表，支持 all 与常见别名。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = [item for item in re.split(r"[\s,;]+", value) if item]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise ValueError("群功能必须是数组或逗号、空格分隔的字符串")

    features: List[str] = []
    for item in items:
        key = FEATURE_ALIASES.get(item.strip().lower(), item.strip().lower())
        if key == ALL_KEYWORD:
            return sorted(ALL_FEATURES)
        if key not in ALL_FEATURES:
            raise ValueError(
                f"未知群功能：{item}；可用功能：{', '.join(FEATURE_NAMES)}、all"
            )
        if key not in features:
            features.append(key)
    return features


def resolve_group_features(
    group_id: int,
    features_map: Dict[int, List[str]],
    video_only_groups: Iterable[int],
) -> FrozenSet[str]:
    """未配置的群保留全部功能；显式配置与旧的仅视频群按白名单生效。"""
    features_map = features_map or {}
    group_id = int(group_id)
    if group_id in features_map:
        return frozenset(features_map[group_id])
    if group_id in {int(item) for item in (video_only_groups or [])}:
        return frozenset({"video"})
    return ALL_FEATURES


def is_group_restricted(
    group_id: int,
    features_map: Dict[int, List[str]],
    video_only_groups: Iterable[int],
) -> bool:
    group_id = int(group_id)
    return group_id in (features_map or {}) or group_id in {
        int(item) for item in (video_only_groups or [])
    }
