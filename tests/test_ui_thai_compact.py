import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ThaiCompactUiTests(unittest.TestCase):
    def test_ui_is_paste_first_and_thai(self):
        text = (ROOT / "ui.py").read_text(encoding="utf-8")
        self.assertIn("วางจากคลิปบอร์ด", text)
        self.assertIn("บันทึก + ไอดีถัดไป", text)
        self.assertIn("เพิ่มไอดีส่ง", text)
        self.assertIn("ตัวรับหลัก", text)
        self.assertIn("next_sender_slot", text)

    def test_main_layout_is_not_old_wide_split(self):
        text = (ROOT / "ui.py").read_text(encoding="utf-8")
        self.assertNotIn('self.geometry("1360x840")', text)
        self.assertNotIn('self.minsize(1120, 720)', text)
        self.assertIn('orient="horizontal"', text)

    def test_run_bat_opens_ui_entrypoint(self):
        text = (ROOT / "run.bat").read_text(encoding="utf-8").lower()
        self.assertIn("main.py", text)
        self.assertNotIn("cmd /k", text)


if __name__ == "__main__":
    unittest.main()
