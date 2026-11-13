from typing import Optional, Protocol

import serial


class _SerialConnection(Protocol):
    @property
    def is_open(self) -> bool: ...

    @property
    def in_waiting(self) -> int: ...

    def write(self, data: bytes) -> int: ...

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class MW10DController:
    """Minimal monitor-only serial interface for the MW-10D."""

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600) -> None:
        self.port = port
        self.baudrate = baudrate
        self._connection: Optional[_SerialConnection] = None

    def connect(self) -> None:
        """Open the configured serial connection using the device's 8-N-1 format."""
        if self._connection is not None and self._connection.is_open:
            raise RuntimeError("The serial connection is already open.")

        serial_class = getattr(serial, "Serial", None)
        if serial_class is None:
            raise RuntimeError(
                "PySerial is unavailable. Install V0 dependencies with "
                "'python -m pip install -r requirements.txt'."
            )

        self._connection = serial_class(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
        )

    def close(self) -> None:
        """Close the serial connection if it is open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def send_readback_request(self) -> None:
        """Send one ACT status-readback request."""
        self._require_connection().write(b"ACT\r")

    def read_available(self) -> bytes:
        """Return bytes currently available from the device without blocking."""
        connection = self._require_connection()
        bytes_waiting = connection.in_waiting
        if bytes_waiting == 0:
            return b""
        return connection.read(bytes_waiting)

    def _require_connection(self) -> _SerialConnection:
        if self._connection is None or not self._connection.is_open:
            raise RuntimeError("The serial connection is not open.")
        return self._connection
