from typing import List, Sequence, TypeVar


DynamicItem = TypeVar("DynamicItem")


def get_dynamic_id(dynamic: DynamicItem) -> int:
    """Return the numeric ID used to order Bilibili dynamics."""
    return int(dynamic.extend.dyn_id_str)


def get_latest_dynamic_id(dynamics: Sequence[DynamicItem]) -> int:
    """Return the newest ID from an unordered, non-empty dynamic list."""
    return max(get_dynamic_id(dynamic) for dynamic in dynamics)


def select_new_dynamics(
    dynamics: Sequence[DynamicItem],
    current_offset: int,
    max_push_per_poll: int,
) -> List[DynamicItem]:
    """Select unseen dynamics in chronological order, optionally capped."""
    dynamics_by_id = {get_dynamic_id(dynamic): dynamic for dynamic in dynamics}
    selected = [
        dynamics_by_id[dynamic_id]
        for dynamic_id in sorted(dynamics_by_id)
        if dynamic_id > current_offset
    ]
    if max_push_per_poll > 0:
        return selected[-max_push_per_poll:]
    return selected
