import unittest
from types import SimpleNamespace

from haruka_bot.dynamic_selection import (
    get_latest_dynamic_id,
    select_new_dynamics,
)


def make_dynamic(dynamic_id: int):
    return SimpleNamespace(
        extend=SimpleNamespace(dyn_id_str=str(dynamic_id)),
    )


def selected_ids(dynamics):
    return [int(dynamic.extend.dyn_id_str) for dynamic in dynamics]


class DynamicSelectionTests(unittest.TestCase):
    def setUp(self):
        self.dynamics = [
            make_dynamic(105),
            make_dynamic(101),
            make_dynamic(104),
            make_dynamic(103),
            make_dynamic(102),
        ]

    def test_latest_id_uses_entire_unordered_page(self):
        self.assertEqual(get_latest_dynamic_id(self.dynamics), 105)

    def test_default_limit_selects_only_latest_unseen_dynamic(self):
        selected = select_new_dynamics(self.dynamics, 100, 1)

        self.assertEqual(selected_ids(selected), [105])

    def test_positive_limit_selects_recent_items_oldest_first(self):
        selected = select_new_dynamics(self.dynamics, 100, 3)

        self.assertEqual(selected_ids(selected), [103, 104, 105])

    def test_zero_limit_selects_all_unseen_items_oldest_first(self):
        selected = select_new_dynamics(self.dynamics, 102, 0)

        self.assertEqual(selected_ids(selected), [103, 104, 105])

    def test_no_unseen_dynamics_returns_empty_list(self):
        selected = select_new_dynamics(self.dynamics, 105, 1)

        self.assertEqual(selected, [])

    def test_single_unseen_dynamic_is_selected(self):
        selected = select_new_dynamics([make_dynamic(101)], 100, 1)

        self.assertEqual(selected_ids(selected), [101])

    def test_duplicate_ids_are_only_selected_once(self):
        dynamics = [make_dynamic(101), make_dynamic(101), make_dynamic(102)]

        selected = select_new_dynamics(dynamics, 100, 0)

        self.assertEqual(selected_ids(selected), [101, 102])


if __name__ == "__main__":
    unittest.main()
