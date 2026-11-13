import argparse
import math
import sys
import time

from controller import MW10DController


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
    parser = argparse.ArgumentParser(description="Poll MW-10D status and print raw serial data.")
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
        help="time between ACT requests (default: 0.2 seconds)",
    )
    return parser.parse_args()


def run(controller: MW10DController, poll_interval: float) -> None:
    next_request = time.monotonic()

    while True:
        now = time.monotonic()
        if now >= next_request:
            controller.send_readback_request()
            next_request = now + poll_interval

        response = controller.read_available()
        if response:
            sys.stdout.buffer.write(response)
            sys.stdout.buffer.flush()

        time.sleep(0.01)


def main() -> int:
    args = parse_args()
    controller = MW10DController(port=args.port, baudrate=args.baudrate)

    try:
        controller.connect()
        print(
            f"Connected to {args.port} at {args.baudrate} baud. Press Ctrl+C to stop.",
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
