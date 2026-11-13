import argparse
from dataclasses import dataclass
import math
from queue import Empty, Queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional, Tuple

from controller import MW10DController


POLL_INTERVAL_SECONDS = 0.05
RESPONSE_TIMEOUT_SECONDS = 1.0
STATUS_STALE_SECONDS = 1.0
MAX_RESPONSE_BYTES = 4096
SUCCESS_MARKER = b"\r\n*"
ERROR_MARKER = b"\r\n\x07?"
MODE_NAMES = {
    "1": "Manual Tune",
    "2": "Analog Remote",
    "3": "Auto Tune",
}
PRESET_VALUES = set("0123456789ABC")
NUMERIC_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


@dataclass(frozen=True)
class ActStatus:
    rdc: str
    rpp: str
    c1: str
    c2: str
    rps: str
    mode: str
    preset: str


def parse_act_payload(payload: bytes) -> Optional[ActStatus]:
    """Parse documented fixed-width ACT data or the observed compact display form."""
    try:
        payload.decode("ascii")
    except UnicodeDecodeError:
        return None

    fields = None
    if len(payload) == 22:
        fields = [
            payload[0:5].decode("ascii").strip(),
            payload[5:10].decode("ascii").strip(),
            payload[10:15].decode("ascii").strip(),
            payload[15:20].decode("ascii").strip(),
            payload[20:22].decode("ascii").strip(),
        ]
    else:
        tokens = payload.replace(b"\r", b" ").replace(b"\n", b" ").decode("ascii").split()
        if len(tokens) == 4 and len(tokens[3]) == 4:
            fields = [tokens[0], tokens[1], tokens[2], tokens[3][:2], tokens[3][2:]]

    if fields is None or any(not field for field in fields):
        return None

    rdc, rpp, c1, c2, rps = fields
    if not NUMERIC_PATTERN.fullmatch(rdc):
        rdc = "Unknown"
    if not NUMERIC_PATTERN.fullmatch(rpp):
        rpp = "Unknown"
    if not c1.isdecimal() or not 0 <= int(c1) <= 99:
        c1 = "Unknown"
    if not c2.isdecimal() or not 0 <= int(c2) <= 99:
        c2 = "Unknown"

    rps = rps.upper()
    if len(rps) != 2 or rps[0] not in MODE_NAMES or rps[1] not in PRESET_VALUES:
        mode = "Unknown"
        preset = "Unknown"
    else:
        mode = MODE_NAMES[rps[0]]
        preset = rps[1]

    return ActStatus(rdc, rpp, c1, c2, rps, mode, preset)


def extract_act_payload(response: bytes) -> Optional[ActStatus]:
    """Remove the echoed ACT command and parse the response body."""
    echo_index = response.find(b"ACT")
    body = response[echo_index + 3 :] if echo_index >= 0 else response
    spaced_newline = body.lstrip(b" ")
    if spaced_newline.startswith((b"\r\n", b"\n")):
        body = spaced_newline
    while body.startswith(b"\r\n"):
        body = body[2:]
    while body.startswith(b"\n"):
        body = body[1:]
    while body.endswith(b"\r\n"):
        body = body[:-2]
    while body.endswith(b"\n"):
        body = body[:-1]
    return parse_act_payload(body)


def parse_operator_command(text: str, mode: str) -> Tuple[str, str]:
    parts = text.strip().split()
    if len(parts) == 1 and parts[0].lower() == "toggle":
        if mode not in ("Manual Tune", "Auto Tune"):
            raise ValueError("Toggle is unavailable unless mode is Manual Tune or Auto Tune.")
        return "Toggle", "TAM"

    if len(parts) == 2 and parts[0].lower() in ("c1", "c2"):
        if not re.fullmatch(r"\d{1,2}", parts[1]):
            raise ValueError("Capacitor position must be an integer from 0 to 99.")
        position = int(parts[1], 10)
        if not 0 <= position <= 99:
            raise ValueError("Capacitor position must be an integer from 0 to 99.")
        if mode != "Manual Tune":
            raise ValueError("C1/C2 commands are allowed only in Manual Tune mode.")
        alias = parts[0].upper() + " " + parts[1]
        command = ("SCO " if parts[0].lower() == "c1" else "SCT ") + str(position)
        return alias, command

    raise ValueError("Use Toggle, C1 X, or C2 X (X is an integer from 0 to 99).")


def positive_seconds(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return interval


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor and control MW-10D over serial.")
    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="serial device name (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--baudrate",
        "--baud",
        type=positive_integer,
        default=9600,
        help="serial baud rate (default: 9600)",
    )
    parser.add_argument(
        "--poll-interval",
        type=positive_seconds,
        default=POLL_INTERVAL_SECONDS,
        metavar="SECONDS",
        help="time between ACT requests (default: 0.05 seconds)",
    )
    return parser.parse_args()


class MW10DApp:
    def __init__(
        self,
        root: tk.Tk,
        controller: MW10DController,
        poll_interval: float,
    ) -> None:
        self.root = root
        self.controller = controller
        self.poll_interval = poll_interval
        self._rx_buffer = bytearray()
        self._pending_kind: Optional[str] = None
        self._pending_command: Optional[str] = None
        self._sent_at = 0.0
        self._next_poll = 0.0
        self._last_status: Optional[ActStatus] = None
        self._last_status_at = 0.0
        self._awaiting_refresh = False
        self._confirmation_id = 0
        self._confirmation_request: Optional[Tuple[int, str, str]] = None
        self._confirmed_command: Optional[Tuple[str, str]] = None
        self._confirmation_requires_status = False
        self._confirmation_requests: Queue = Queue()
        self._confirmation_results: Queue = Queue()
        self._closing = False
        self._serial_failed = False

        self._build_window()
        self._confirmation_thread = threading.Thread(
            target=self._read_confirmation,
            daemon=True,
        )
        self._confirmation_thread.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(0, self._tick)

    def _build_window(self) -> None:
        self.root.title("MW-10D V1.5")
        self.root.minsize(420, 290)

        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="MW-10D status", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        self._value_labels = {}
        labels = (
            ("RDC - DC bias voltage", "rdc"),
            ("RPP - peak-to-peak voltage", "rpp"),
            ("RCO - C1 position (% meshed)", "c1"),
            ("RCT - C2 position (% meshed)", "c2"),
            ("RPS - mode / preset", "rps"),
        )
        for row, (title, key) in enumerate(labels, start=1):
            ttk.Label(frame, text=title + ":").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=2
            )
            value = ttk.Label(frame, text="Unknown")
            value.grid(row=row, column=1, sticky="w", pady=2)
            self._value_labels[key] = value

        self._mode_label = ttk.Label(frame, text="Mode: Unknown")
        self._mode_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 8))

        ttk.Separator(frame).grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(
            frame,
            text="Commands: Toggle, C1 0-99, C2 0-99",
        ).grid(row=8, column=0, columnspan=2, sticky="w")
        self._command_entry = ttk.Entry(frame)
        self._command_entry.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(4, 4))
        self._command_entry.bind("<Return>", self._submit_command)
        self._submit_button = ttk.Button(
            frame,
            text="Submit",
            command=lambda: self._submit_command(),
        )
        self._submit_button.grid(row=10, column=1, sticky="e")
        self._message_label = ttk.Label(frame, text="Reading initial device status...")
        self._message_label.grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _read_confirmation(self) -> None:
        while True:
            request = self._confirmation_requests.get()
            if request is None:
                return

            request_id, _alias, wire_command = request
            print(
                f"\nDo you want to send {wire_command} command? (y/n): ",
                end="",
                file=sys.stderr,
                flush=True,
            )
            while True:
                answer = sys.stdin.readline()
                if not answer:
                    self._confirmation_results.put((request_id, False, "No terminal input; cancelled."))
                    break
                answer = answer.strip().lower()
                if answer in ("y", "yes"):
                    self._confirmation_results.put((request_id, True, ""))
                    break
                if answer in ("n", "no"):
                    self._confirmation_results.put((request_id, False, "Cancelled."))
                    break
                print("Please enter y or n: ", end="", file=sys.stderr, flush=True)

    def _submit_command(self, _event=None) -> None:
        if self._writes_blocked():
            self._set_message("Writes are blocked until a fresh, valid mode readback is available.")
            return

        text = self._command_entry.get()
        self._command_entry.delete(0, tk.END)
        try:
            alias, wire_command = parse_operator_command(text, self._current_mode())
        except ValueError as error:
            self._set_message(str(error))
            return

        self._confirmation_id += 1
        request = (self._confirmation_id, alias, wire_command)
        self._confirmation_request = request
        self._confirmation_requests.put(request)
        self._set_message(f"Waiting for terminal confirmation to send {wire_command}.")
        self._update_controls()

    def _process_confirmation_results(self) -> None:
        while True:
            try:
                request_id, approved, message = self._confirmation_results.get_nowait()
            except Empty:
                return

            request = self._confirmation_request
            if request is None or request_id != request[0]:
                continue
            self._confirmation_request = None
            if not approved:
                self._set_message(message)
                self._update_controls()
                continue

            self._confirmed_command = (request[1], request[2])
            self._confirmation_requires_status = True
            self._set_message(f"Confirmed {request[2]}; waiting for the current ACT response.")
            self._try_send_confirmed_command()

    def _try_send_confirmed_command(self) -> None:
        if self._confirmed_command is None or self._pending_kind is not None:
            return
        if self._serial_failed:
            self._confirmed_command = None
            self._set_message("Mode is unknown or stale; confirmed command was not sent.")
            self._update_controls()
            return
        if self._confirmation_requires_status:
            self._confirmation_requires_status = False
            self._start_act_request()
            return
        if self._status_is_stale() or self._awaiting_refresh:
            self._confirmed_command = None
            self._set_message("Mode is unknown or stale; confirmed command was not sent.")
            self._update_controls()
            return

        alias, wire_command = self._confirmed_command
        mode = self._current_mode()
        if alias == "Toggle":
            if mode not in ("Manual Tune", "Auto Tune"):
                self._confirmed_command = None
                self._set_message("Toggle was not sent: mode is not Manual Tune or Auto Tune.")
                self._update_controls()
                return
        elif mode != "Manual Tune":
            self._confirmed_command = None
            self._set_message(f"{alias} was not sent: device is no longer in Manual Tune mode.")
            self._update_controls()
            return

        self._confirmed_command = None
        try:
            self.controller.send_command(wire_command)
        except (OSError, RuntimeError, ValueError) as error:
            self._handle_serial_error(error)
            return
        self._pending_kind = "write"
        self._pending_command = wire_command
        self._sent_at = time.monotonic()
        self._awaiting_refresh = True
        self._rx_buffer.clear()
        self._set_message(f"Sent {wire_command}; waiting for device acknowledgement and fresh ACT data.")
        self._update_controls()

    def _current_mode(self) -> str:
        if self._status_is_stale():
            return "Unknown"
        return self._last_status.mode if self._last_status is not None else "Unknown"

    def _status_is_stale(self) -> bool:
        return (
            self._last_status is None
            or time.monotonic() - self._last_status_at >= STATUS_STALE_SECONDS
        )

    def _writes_blocked(self) -> bool:
        return (
            self._serial_failed
            or self._status_is_stale()
            or self._pending_kind == "write"
            or self._awaiting_refresh
            or self._confirmation_request is not None
            or self._confirmed_command is not None
        )

    def _read_serial(self) -> None:
        data = self.controller.read_available()
        if not data:
            return
        output = getattr(sys.stdout, "buffer", None)
        if output is not None:
            output.write(data)
            output.flush()
        else:
            sys.stdout.write(data.decode("ascii", errors="replace"))
            sys.stdout.flush()
        self._rx_buffer.extend(data)

        while self._pending_kind is not None:
            completion = self._find_completion()
            if completion is None:
                if len(self._rx_buffer) > MAX_RESPONSE_BYTES:
                    self._rx_buffer.clear()
                    self._fail_pending_response("Device response exceeded the expected size.")
                return
            end, marker, succeeded = completion
            response = bytes(self._rx_buffer[:end])
            del self._rx_buffer[: end + len(marker)]
            self._complete_response(response, succeeded)

    def _find_completion(self):
        markers = (
            (SUCCESS_MARKER, True),
            (ERROR_MARKER, False),
        )
        found = []
        for marker, succeeded in markers:
            index = self._rx_buffer.find(marker)
            if index >= 0:
                found.append((index, marker, succeeded))
        if not found:
            return None
        return min(found, key=lambda item: item[0])

    def _complete_response(self, response: bytes, succeeded: bool) -> None:
        kind = self._pending_kind
        command = self._pending_command
        self._pending_kind = None
        self._pending_command = None
        self._sent_at = 0.0

        if kind == "act":
            if succeeded:
                status = extract_act_payload(response)
                if status is not None:
                    self._last_status = status
                    self._last_status_at = time.monotonic()
                    self._awaiting_refresh = False
                    self._set_message("ACT status updated.")
                else:
                    self._last_status = None
                    self._awaiting_refresh = True
                    self._set_message("ACT response did not match a supported format; writes are blocked.")
            else:
                self._last_status = None
                self._awaiting_refresh = True
                self._set_message("Device rejected ACT; writes are blocked.")
            self._next_poll = time.monotonic() + self.poll_interval
        elif kind == "write":
            if succeeded and command and command.encode("ascii") in response:
                self._set_message(f"{command} acknowledged; waiting for a fresh ACT readback.")
            elif succeeded:
                self._set_message("Missing command echo; write outcome is uncertain. Waiting for ACT.")
            else:
                self._set_message(f"Device rejected {command}; checking status with ACT.")
            self._next_poll = time.monotonic()

        self._update_status_display()
        self._try_send_confirmed_command()
        self._update_controls()

    def _fail_pending_response(self, message: str) -> None:
        kind = self._pending_kind
        self._pending_kind = None
        self._pending_command = None
        self._rx_buffer.clear()
        self._awaiting_refresh = True
        self._last_status = None
        self._next_poll = time.monotonic() + self.poll_interval
        if kind == "write":
            message = "Write outcome is unknown. " + message
        self._set_message(message + " Writes are blocked until a fresh ACT response.")
        self._update_status_display()
        self._update_controls()

    def _start_act_request(self) -> None:
        try:
            self.controller.send_readback_request()
        except (OSError, RuntimeError, ValueError) as error:
            self._handle_serial_error(error)
            return
        self._rx_buffer.clear()
        self._pending_kind = "act"
        self._pending_command = "ACT"
        self._sent_at = time.monotonic()

    def _tick(self) -> None:
        if self._closing:
            return
        if not self._serial_failed:
            try:
                self._read_serial()
            except (OSError, RuntimeError, ValueError) as error:
                self._handle_serial_error(error)

        now = time.monotonic()
        if self._pending_kind is not None and now - self._sent_at >= RESPONSE_TIMEOUT_SECONDS:
            self._fail_pending_response(f"Timed out waiting for {self._pending_command} response.")

        self._process_confirmation_results()
        if self._pending_kind is None and self._confirmed_command is not None:
            self._try_send_confirmed_command()

        if self._last_status is not None and self._status_is_stale():
            self._last_status = None
            self._awaiting_refresh = True
            self._set_message("ACT status is stale; writes are blocked.")

        if (
            not self._serial_failed
            and self._pending_kind is None
            and self._confirmation_request is None
            and self._confirmed_command is None
            and time.monotonic() >= self._next_poll
        ):
            self._start_act_request()

        self._update_status_display()
        self._update_controls()
        self.root.after(10, self._tick)

    def _update_status_display(self) -> None:
        if self._status_is_stale():
            for label in self._value_labels.values():
                label.configure(text="Unknown")
            self._mode_label.configure(text="Mode: Unknown")
            return

        status = self._last_status
        if status is None:
            return
        self._value_labels["rdc"].configure(text=status.rdc)
        self._value_labels["rpp"].configure(text=status.rpp)
        self._value_labels["c1"].configure(text=status.c1)
        self._value_labels["c2"].configure(text=status.c2)
        self._value_labels["rps"].configure(text=f"{status.rps} (preset {status.preset})")
        self._mode_label.configure(text=f"Mode: {status.mode} (preset {status.preset})")

    def _update_controls(self) -> None:
        blocked = self._writes_blocked()
        state = "disabled" if blocked else "normal"
        self._command_entry.configure(state=state)
        self._submit_button.configure(state=state)

    def _set_message(self, message: str) -> None:
        self._message_label.configure(text=message)

    def _handle_serial_error(self, error: Exception) -> None:
        self._serial_failed = True
        self._last_status = None
        self._pending_kind = None
        self._pending_command = None
        self._awaiting_refresh = True
        self._set_message(f"Serial error: {error}. Writes are disabled.")
        print(f"Serial error: {error}", file=sys.stderr)
        self._update_status_display()
        self._update_controls()
        try:
            self.controller.close()
        except (OSError, RuntimeError, ValueError) as close_error:
            print(f"Serial close error: {close_error}", file=sys.stderr)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._confirmation_requests.put(None)
        try:
            self.controller.close()
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Serial close error: {error}", file=sys.stderr)
        self.root.destroy()


def main() -> int:
    args = parse_args()
    controller = MW10DController(port=args.port, baudrate=args.baudrate)
    try:
        controller.connect()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Serial error: {error}", file=sys.stderr)
        return 1

    try:
        root = tk.Tk()
    except tk.TclError as error:
        controller.close()
        print(f"Tkinter error: {error}", file=sys.stderr)
        return 1

    MW10DApp(root, controller, args.poll_interval)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
