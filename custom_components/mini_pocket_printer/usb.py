"""Transport USB.

Mêmes commandes 10 FF qu'en Bluetooth, seul le flux change.

Deux chemins selon ce que le noyau expose : /dev/usb/lp0 avec le pilote
usblp, ou les endpoints bulk via libusb. Le périphérique s'annonce en
09C7:0020.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os

_LOGGER = logging.getLogger(__name__)

VID, PID = 0x09C7, 0x0020
# Blocs volontairement petits : la tête thermique consomme lentement, et un
# bloc trop gros sature la file du pilote.
CHUNK = 1024
READ_SIZE = 64


def list_devices() -> list[dict]:
    """Périphériques utilisables, du plus simple au plus complexe.

    Retourne des dicts avec une clé 'path' à stocker dans la configuration
    et un 'label' lisible pour le selecteur.
    """
    found: list[dict] = []

    for path in sorted(glob.glob("/dev/usb/lp*")):
        found.append({
            "path": path,
            "label": f"{path} (pilote usblp)",
            "kind": "chardev",
        })

    for path in sorted(glob.glob("/dev/ttyACM*")):
        found.append({
            "path": path,
            "label": f"{path} (port serie)",
            "kind": "serial",
        })

    for device in _libusb_devices():
        found.append(device)

    return found


def _libusb_devices() -> list[dict]:
    """Périphériques 09C7:0020 visibles par libusb, si pyusb est installé."""
    try:
        import usb.core
    except ImportError:
        return []

    devices = []
    try:
        for device in usb.core.find(find_all=True, idVendor=VID, idProduct=PID):
            path = f"usb:{device.bus:03d}:{device.address:03d}"
            devices.append({
                "path": path,
                "label": f"{path} (libusb {VID:04x}:{PID:04x})",
                "kind": "libusb",
            })
    except Exception:  # noqa: BLE001 - libusb absent ou permissions
        _LOGGER.debug("Enumeration libusb impossible", exc_info=True)
    return devices


class UsbConnection:
    """Écriture et lecture sur le périphérique, hors boucle d'evenements."""

    def __init__(self, hass, path: str) -> None:
        self._hass = hass
        self.path = path
        self._fd: int | None = None
        self._usb = None
        self._out = None
        self._in = None

    @property
    def is_libusb(self) -> bool:
        return self.path.startswith("usb:")

    async def connect(self) -> None:
        if self.is_libusb:
            await self._hass.async_add_executor_job(self._open_libusb)
        else:
            self._fd = await self._hass.async_add_executor_job(
                os.open, self.path, os.O_RDWR | os.O_NONBLOCK
            )
        _LOGGER.debug("USB ouvert : %s", self.path)

    def _open_libusb(self) -> None:
        import usb.core
        import usb.util

        _, bus, address = self.path.split(":")
        device = None
        for candidate in usb.core.find(find_all=True, idVendor=VID, idProduct=PID):
            if (candidate.bus, candidate.address) == (int(bus), int(address)):
                device = candidate
                break
        if device is None:
            raise OSError(f"Peripherique USB {self.path} introuvable")

        configuration = device.get_active_configuration()
        for interface in configuration:
            out = in_ = None
            for endpoint in interface:
                if usb.util.endpoint_type(endpoint.bmAttributes) != \
                        usb.util.ENDPOINT_TYPE_BULK:
                    continue
                if usb.util.endpoint_direction(endpoint.bEndpointAddress) == \
                        usb.util.ENDPOINT_OUT:
                    out = endpoint
                else:
                    in_ = endpoint
            if out is not None:
                self._usb, self._out, self._in = device, out, in_
                return
        raise OSError("Aucun endpoint bulk sur ce peripherique")

    async def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            await self._hass.async_add_executor_job(os.close, fd)
        self._usb = self._out = self._in = None

    def _write_all(self, data: bytes) -> None:
        """Écrit la totalite du bloc.

        os.write n'écrit pas forcément tout : sur un descripteur non bloquant,
        il s'arrête des que la file du pilote est pleine et retourne le nombre
        d'octets reellement pris. Ignorer cette valeur perd des données en
        plein milieu du bitmap, et l'impression s'interrompt.
        """
        import time

        view = memoryview(data)
        deadline = time.time() + 30
        while view:
            try:
                written = os.write(self._fd, view)
            except BlockingIOError:
                if time.time() > deadline:
                    raise OSError("Ecriture USB bloquee : file pleine")
                time.sleep(0.01)
                continue
            if written <= 0:
                raise OSError("Ecriture USB interrompue")
            view = view[written:]

    async def send(self, data: bytes, delay: float = 0.0) -> None:
        for index in range(0, len(data), CHUNK):
            piece = data[index:index + CHUNK]
            if self.is_libusb:
                # pyusb retourne aussi le nombre d'octets ecrits.
                sent = await self._hass.async_add_executor_job(
                    self._out.write, piece
                )
                if sent is not None and sent < len(piece):
                    raise OSError(
                        f"Ecriture USB partielle : {sent}/{len(piece)} octets"
                    )
            else:
                await self._hass.async_add_executor_job(self._write_all, piece)
            # Laisse le firmware digerer : sans pause, la file deborde et
            # l'impression s'arrête en cours de bitmap.
            await asyncio.sleep(delay or 0.01)

    async def read(self, timeout: float = 1.0) -> bytes:
        """Lit une réponse, ou rien si le délai expire."""
        if self.is_libusb:
            def _read() -> bytes:
                import usb.core
                try:
                    return bytes(self._in.read(READ_SIZE,
                                               timeout=int(timeout * 1000)))
                except usb.core.USBError:
                    return b""

            return b"" if self._in is None else \
                await self._hass.async_add_executor_job(_read)

        def _read_fd() -> bytes:
            import time

            data = b""
            end = time.time() + timeout
            while time.time() < end:
                try:
                    chunk = os.read(self._fd, READ_SIZE)
                except BlockingIOError:
                    time.sleep(0.05)
                    continue
                except OSError:
                    break
                if chunk:
                    data += chunk
                    end = time.time() + 0.2
            return data

        return await self._hass.async_add_executor_job(_read_fd)

    async def query(self, payload: bytes, timeout: float = 1.5) -> bytes:
        await self.send(payload)
        return await self.read(timeout=timeout)
