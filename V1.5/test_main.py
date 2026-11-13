import unittest

from main import extract_act_payload, parse_act_payload, parse_operator_command


class ActPayloadTests(unittest.TestCase):
    def test_parses_documented_fixed_width_packet(self) -> None:
        payload = b" 0.0 " + b" 0.0 " + b"   75" + b"   34" + b"11"

        status = parse_act_payload(payload)

        self.assertEqual(len(payload), 22)
        self.assertIsNotNone(status)
        self.assertEqual(
            (status.rdc, status.rpp, status.c1, status.c2, status.rps),
            ("0.0", "0.0", "75", "34", "11"),
        )
        self.assertEqual((status.mode, status.preset), ("Manual Tune", "1"))

    def test_parses_observed_compact_packet(self) -> None:
        status = parse_act_payload(b"0 0 75 3411")

        self.assertIsNotNone(status)
        self.assertEqual(
            (status.rdc, status.rpp, status.c1, status.c2, status.rps),
            ("0", "0", "75", "34", "11"),
        )

    def test_extracts_status_after_command_echo(self) -> None:
        status = extract_act_payload(b"* ACT\r\n0 0 75 3411")

        self.assertIsNotNone(status)
        self.assertEqual((status.mode, status.preset), ("Manual Tune", "1"))

    def test_recognizes_remote_mode_and_rejects_malformed_packet(self) -> None:
        status = parse_act_payload(b"0 0 75 3421")

        self.assertIsNotNone(status)
        self.assertEqual(status.mode, "Analog Remote")
        self.assertIsNone(parse_act_payload(b"not an ACT packet"))


class OperatorCommandTests(unittest.TestCase):
    def test_maps_toggle_and_capacitor_aliases(self) -> None:
        self.assertEqual(parse_operator_command("Toggle", "Auto Tune"), ("Toggle", "TAM"))
        self.assertEqual(parse_operator_command("C1 01", "Manual Tune"), ("C1 01", "SCO 1"))
        self.assertEqual(parse_operator_command("C2 99", "Manual Tune"), ("C2 99", "SCT 99"))

    def test_rejects_writes_outside_allowed_modes_or_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_operator_command("Toggle", "Analog Remote")
        with self.assertRaises(ValueError):
            parse_operator_command("C1 50", "Auto Tune")
        with self.assertRaises(ValueError):
            parse_operator_command("C2 100", "Manual Tune")


if __name__ == "__main__":
    unittest.main()
