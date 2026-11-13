import argparse
import math
from queue import Empty, Queue
import sys
import threading
import time

from controller import MW10DController


COMPLETION_MARKER = b"\r\n*"
STOP_TIMEOUT_SECONDS = 2.0


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
    parser = argparse.ArgumentParser(description="Monitor MW-10D status over serial.")
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
        default=0.2,
        metavar="SECONDS",
        help="time between ACT requests in polling mode (default: 0.2 seconds)",
    )
    return parser.parse_args()


def read_terminal_commands(commands: Queue) -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        commands.put(line.strip().lower())


def write_response(response: bytes) -> None:
    sys.stdout.buffer.write(response)
    sys.stdout.buffer.flush()


def run(controller: MW10DController, poll_interval: float) -> None:
    commands = Queue()
    input_thread = threading.Thread(
        target=read_terminal_commands,
        args=(commands,),
        daemon=True,
    )
    input_thread.start()

    mode = "listen"
    completion_tail = b""
    next_poll = 0.0
    stop_deadline = 0.0
    controller.start_continuous_readback()

    try:
        while True:
            try:
                command = commands.get_nowait()
            except Empty:
                command = ""

            if command == "t":
                if mode == "listen":
                    controller.stop_continuous_readback()
                    mode = "stopping"
                    completion_tail = b""
                    stop_deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
                    print("Stopping continuous readback...", file=sys.stderr)
                elif mode == "poll":
                    controller.start_continuous_readback()
                    mode = "listen"
                    print("Raw-listen mode.", file=sys.stderr)
                else:
                    print("Wait for continuous readback to stop before toggling.", file=sys.stderr)
            elif command:
                print("Type t and press Enter to toggle modes.", file=sys.stderr)

            response = controller.read_available()
            if response:
                write_response(response)

                if mode == "stopping":
                    combined = completion_tail + response
                    if COMPLETION_MARKER in combined:
                        mode = "poll"
                        next_poll = time.monotonic()
                        completion_tail = b""
                        print("Polling mode.", file=sys.stderr)
                    else:
                        completion_tail = combined[-(len(COMPLETION_MARKER) - 1) :]

            now = time.monotonic()
            if mode == "stopping" and now >= stop_deadline:
                raise RuntimeError("Timed out waiting for continuous readback to stop.")

            if mode == "poll" and now >= next_poll:
                controller.send_readback_request()
                next_poll = now + poll_interval

            time.sleep(0.01)
    finally:
        if mode == "listen":
            controller.stop_continuous_readback()


def main() -> int:
    args = parse_args()
    controller = MW10DController(port=args.port, baudrate=args.baudrate)

    try:
        controller.connect()
        print(
            "Raw-listen mode. Type t and press Enter to switch modes; "
            "press Ctrl+C to exit.",
            file=sys.stderr,
        )
        run(controller, args.poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Serial error: {error}", file=sys.stderr)
        return 1
    finally:
        controller.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
