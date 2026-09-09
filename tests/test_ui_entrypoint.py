import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiEntrypointTests(unittest.TestCase):
    def test_main_is_ui_entrypoint(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from ui import main as ui_main", main)
        self.assertNotIn("argparse", main)
        self.assertNotIn("add_subparsers", main)

    def test_bat_launches_main(self):
        bat = (ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn("main.py", bat)
        self.assertNotIn("run_cli", bat)

    def test_full_ui_controls_exist(self):
        ui = (ROOT / "ui.py").read_text(encoding="utf-8")
        for label in (
            "ตั้งค่าตัวรับ",
            "เพิ่มไอดีส่ง",
            "นำเข้าโฟลเดอร์",
            "เริ่มที่เลือก",
            "เริ่มทั้งหมด",
            "หยุดหลังไอดีนี้",
            "รายชื่อเพื่อน",
            "อ่านกล่องใจ",
            "ตั้งค่าการทำงาน",
            "วางจากคลิปบอร์ด",
            "บันทึก + ไอดีถัดไป",
        ):
            self.assertIn(label, ui)


if __name__ == "__main__":
    unittest.main()
