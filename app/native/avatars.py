from __future__ import annotations

import time
from collections import OrderedDict

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class AvatarLoader(QObject):
    MAX_BYTES = 1024 * 1024

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.manager = QNetworkAccessManager(self)
        self.cache = OrderedDict()
        self.active = {}
        self.pending = []
        self.failed = OrderedDict()
        self.closed = False

    def refresh(self):
        if self.closed:
            return
        with self.state.lock:
            stage = self.state.config.get("stage") or {}
            urls = list(dict.fromkeys(sender["avatar"] for sender in self.state.gift_senders.values()
                                     if sender.get("avatar"))) if stage.get("show_ballpit", True) else []
            avatar = ((self.state.gift_show.get("active") or {}).get("user") or {}).get("avatar")
            if avatar and avatar not in urls:
                urls.insert(0, avatar)
            avatar = ((self.state.fishing.get("net") or {}).get("user") or {}).get("avatar")
            if avatar and avatar not in urls:
                urls.insert(0, avatar)
        self.pending = []
        for url in urls:
            parsed = QUrl(url)
            if parsed.scheme() not in ("http", "https") or not parsed.host() or parsed.userInfo():
                continue
            if url in self.cache:
                self.cache.move_to_end(url)
                self.state.set_avatar(url, self.cache[url])
            elif url not in self.active and time.monotonic() >= self.failed.get(url, 0):
                self.pending.append(url)
        self._start()

    def _start(self):
        while self.pending and len(self.active) < 4 and not self.closed:
            url = self.pending.pop(0)
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(5000)
            request.setMaximumRedirectsAllowed(3)
            request.setRawHeader(b"User-Agent", b"LiveStage/1.0")
            request.setRawHeader(b"Accept", b"image/png,image/jpeg,image/webp")
            reply = self.manager.get(request)
            reply.setReadBufferSize(65536)
            timer = QTimer(reply)
            timer.setSingleShot(True)
            timer.timeout.connect(reply.abort)
            timer.start(7000)
            self.active[url] = (reply, bytearray())
            reply.readyRead.connect(lambda address=url: self._read(address))
            reply.finished.connect(lambda address=url: self._finish(address))

    def _read(self, url):
        item = self.active.get(url)
        if not item:
            return
        reply, data = item
        data.extend(bytes(reply.readAll()))
        if len(data) > self.MAX_BYTES:
            reply.abort()

    def _finish(self, url):
        if url not in self.active:
            return
        self._read(url)
        reply, data = self.active.pop(url)
        image = None
        if not self.closed and reply.error() == QNetworkReply.NetworkError.NoError and len(data) <= self.MAX_BYTES:
            buffer = QBuffer()
            buffer.setData(QByteArray(bytes(data)))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            dimensions = reader.size()
            if (bytes(reader.format()).lower() in (b"png", b"jpeg", b"jpg", b"webp")
                    and 0 < dimensions.width() <= 2048 and 0 < dimensions.height() <= 2048):
                reader.setScaledSize(dimensions.scaled(QSize(128, 128), Qt.AspectRatioMode.KeepAspectRatio))
                decoded = reader.read()
                if not decoded.isNull():
                    side = min(decoded.width(), decoded.height())
                    image = decoded.copy((decoded.width() - side) // 2, (decoded.height() - side) // 2, side, side)
        if image is not None:
            self.cache[url] = image
            self.cache.move_to_end(url)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            self.state.set_avatar(url, image)
        else:
            self.failed[url] = time.monotonic() + 30
            while len(self.failed) > 64:
                self.failed.popitem(last=False)
        reply.deleteLater()
        self._start()

    def close(self):
        self.closed = True
        self.pending.clear()
        for reply, data in list(self.active.values()):
            reply.abort()
