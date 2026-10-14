"""select_model với hộp chọn model KIỂU MỚI của Suno (LIVE 2026-10-12): nút model
có aria-haspopup='dialog', mở [role=dialog] > [role=grid][aria-label=Model] >
[role=row][data-grid-list-row] (aria-selected). Lỗi gốc: selector cũ
`button[aria-haspopup='menu']:right-of(Credits)` khớp nhầm nút ⋯ của danh sách bài
→ «Không đặt được model 'v6' (hiện: '')». Trang giả lập — không mở trình duyệt."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.video import config as vconfig
from backend.video.suno_driver import SunoDriver, SunoUIChanged

ROWS = [
    ("Create Custom Model\nBeta\nCreate a model based on your uploads (100 Credits)", None),
    ("Bamboo Flute v1", "false"),
    ("v6\nPro\nPowerful. Versatile. Refined. Our best model yet.", "false"),
    ("v6-wild\nPro\nBest for experimental ideas.", "false"),
    ("v6-mini\nA free, more efficient version of premium v6 models.", "false"),
]


class _Keyboard:
    def __init__(self, ui):
        self.ui = ui

    def press(self, key):
        if key == "Escape":
            self.ui.open = False


class _Row:
    def __init__(self, ui, i):
        self.ui, self.i = ui, i

    def inner_text(self):
        return ROWS[self.i][0]

    def get_attribute(self, name):
        if name == "aria-selected":
            if ROWS[self.i][1] is None:
                return None
            return "true" if self.ui.selected == self.i else "false"
        return None

    def click(self):
        self.ui.clicked.append(ROWS[self.i][0].split("\n")[0])
        self.ui.selected = self.i
        self.ui.open = False        # Suno đóng hộp sau khi chọn


class _Items:
    def __init__(self, ui):
        self.ui = ui

    def count(self):
        return len(ROWS) if self.ui.open else 0

    def nth(self, i):
        return _Row(self.ui, i)

    @property
    def first(self):
        return self

    def wait_for(self, state=None, timeout=None):
        if not self.ui.open:
            raise TimeoutError("model dialog not open")


class _Button:
    def __init__(self, ui):
        self.ui = ui

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.ui.has_button else 0

    def is_visible(self):
        return self.ui.has_button

    def inner_text(self):
        return ROWS[self.ui.selected][0].split("\n")[0]

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def bounding_box(self):
        return None                 # → _real_click False → locator.click()

    def click(self):
        self.ui.open = True


class FakeModelPage:
    def __init__(self, selected="v6", has_button=True):
        names = [r[0].split("\n")[0] for r in ROWS]
        self.selected = names.index(selected)
        self.has_button = has_button
        self.open = False
        self.clicked: list[str] = []
        self.keyboard = _Keyboard(self)
        self.evals: list[str] = []

    def evaluate(self, js):
        self.evals.append(js)
        return self.has_button      # JS đánh dấu nút cạnh pill Credits

    def locator(self, sel):
        if sel == "button[data-mm-model-btn='1']":
            return _Button(self)
        if sel == SunoDriver._MODEL_ITEMS:
            return _Items(self)
        raise AssertionError(f"selector lạ: {sel}")


class SunoModelSelectTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("backend.video.suno_driver.wait_ms", lambda p, ms: None),
                              ("backend.video.suno_driver.query_first",
                               lambda page, sels, timeout_ms=0: None)):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        self.logs: list[str] = []

    def _driver(self, page):
        return SunoDriver(page, selectors={"model_button": ["x"]}, log=self.logs.append)

    def test_switches_back_to_v6_from_wild(self):
        page = FakeModelPage(selected="v6-wild")
        self.assertEqual(self._driver(page).select_model("v6"), "v6")
        self.assertEqual(page.clicked, ["v6"])
        self.assertFalse(page.open)

    def test_v6_already_selected_clicks_nothing(self):
        page = FakeModelPage(selected="v6")
        self.assertEqual(self._driver(page).select_model("v6"), "v6")
        self.assertEqual(page.clicked, [])
        self.assertFalse(page.open, "hộp model phải được đóng lại")

    def test_never_offers_create_custom_model_row(self):
        page = FakeModelPage(selected="v6")
        d = self._driver(page)
        page.open = True
        names = [n for n, _, _ in d._model_items()]
        self.assertNotIn("Create Custom Model", names)
        self.assertEqual(names, ["Bamboo Flute v1", "v6", "v6-wild", "v6-mini"])
        with self.assertRaises(SunoUIChanged):
            d.select_model("Create Custom Model")
        self.assertEqual(page.clicked, [])

    def test_reads_selection_from_aria_selected(self):
        page = FakeModelPage(selected="Bamboo Flute v1")
        self.assertEqual(self._driver(page).current_model(), "Bamboo Flute v1")

    def test_custom_model_selected_switches_to_v6(self):
        page = FakeModelPage(selected="Bamboo Flute v1")
        self.assertEqual(self._driver(page).select_model("v6"), "v6")
        self.assertEqual(page.clicked, ["v6"])

    def test_missing_button_still_refuses_wrong_model(self):
        page = FakeModelPage(has_button=False)
        with patch("backend.video.suno_driver.time.time",
                   side_effect=range(0, 10**6, 2)), self.assertRaises(SunoUIChanged):
            self._driver(page).select_model("v6", allow_fallback=False)

    def test_selectors_follow_new_dialog_ui(self):
        sel = vconfig.get_selectors("suno")
        self.assertFalse(any("aria-haspopup='menu'" in s for s in sel["model_button"]),
                         "selector 'menu' cũ khớp nhầm nút ⋯ của danh sách bài")
        self.assertIn("[role=grid][aria-label='Model'] [role=row]", SunoDriver._MODEL_ITEMS)
        self.assertIn("[role=menu] [role=menuitemradio]", SunoDriver._MODEL_ITEMS)
        self.assertIn("aria-haspopup", SunoDriver._JS_MARK_MODEL_BUTTON)
        self.assertIn("Credits remaining", SunoDriver._JS_MARK_MODEL_BUTTON)


if __name__ == "__main__":
    unittest.main()
