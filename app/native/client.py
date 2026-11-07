from __future__ import annotations

import json
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket

from .state import StageState
from .avatars import AvatarLoader


class LiveClient(QObject):
    message = Signal(dict)
    status = Signal(str)
    connection = Signal(bool)
    response = Signal(str, dict)

    def __init__(self, state: StageState, base_url: str):
        super().__init__()
        self.state, self.base_url = state, base_url.rstrip("/")
        self.socket = QWebSocket()
        self.manager = QNetworkAccessManager(self)
        self.avatars = AvatarLoader(state, self)
        self.socket.connected.connect(self._connected)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.errorOccurred.connect(self._socket_error)
        self.socket.textMessageReceived.connect(self._received)
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(self.open)
        self.heartbeat = QTimer(self)
        self.heartbeat.timeout.connect(self._ping)
        self.heartbeat.start(10000)
        self.closing = False
        self.last_message = time.monotonic()
        self.retries = 0

    def open(self):
        if self.closing:
            return
        url = QUrl(self.base_url + "/ws")
        url.setScheme("wss" if url.scheme() == "https" else "ws")
        self.socket.open(url)

    def _connected(self):
        if self.closing:
            self.socket.close()
            return
        self.retry.stop()
        self.retries = 0
        self.last_message = time.monotonic()
        with self.state.lock:
            self.state.connected = True
        self.status.emit("直播服务已连接")
        self.connection.emit(True)
        self.send({"cmd": "renderer_ready"})

    def _disconnected(self):
        with self.state.lock:
            self.state.connected = False
        self.connection.emit(False)
        self.message.emit({"type": "speech_interrupt"})
        self.status.emit("服务连接中断，正在自动重连")
        if not self.closing and not self.retry.isActive():
            self.retries += 1
            self.retry.start(min(10000, 1000 * self.retries))

    def _socket_error(self, error):
        if self.socket.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._disconnected()

    def _received(self, raw):
        if self.closing:
            return
        self.last_message = time.monotonic()
        try:
            message = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(message, dict):
            return
        self.state.apply(message)
        if message.get("type") in ("event", "config_changed", "hello", "reset", "stats", "gift_ranking", "gift_show"):
            self.avatars.refresh()
        self.message.emit(message)

    def _ping(self):
        if self.socket.state() == QAbstractSocket.SocketState.ConnectedState:
            if time.monotonic() - self.last_message > 35:
                self.socket.abort()
            else:
                self.send({"cmd": "ping"})

    def send(self, message) -> bool:
        if self.socket.state() != QAbstractSocket.SocketState.ConnectedState:
            self.status.emit("连接尚未就绪，输入内容已保留")
            return False
        self.socket.sendTextMessage(json.dumps(message, ensure_ascii=False))
        return True

    def post(self, route: str, body: dict | None = None):
        request = QNetworkRequest(QUrl(self.base_url + route))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setTransferTimeout(10000)
        reply = self.manager.post(request, json.dumps(body or {}, ensure_ascii=False).encode("utf-8"))

        def completed():
            try:
                result = json.loads(bytes(reply.readAll()))
                if not result.get("ok"):
                    self.status.emit(result.get("error") or "操作未完成")
                else:
                    self.status.emit(result.get("msg") or "操作已完成")
                self.response.emit("POST " + route, result)
            except (ValueError, TypeError):
                self.status.emit("操作失败：" + reply.errorString())
                self.response.emit("POST " + route, {"ok": False, "error": reply.errorString()})
            finally:
                reply.deleteLater()

        reply.finished.connect(completed)

    def get(self, route: str):
        request = QNetworkRequest(QUrl(self.base_url + route))
        request.setTransferTimeout(10000)
        reply = self.manager.get(request)

        def completed():
            try:
                if reply.error() != QNetworkReply.NetworkError.NoError:
                    self.status.emit("读取失败：" + reply.errorString())
                    self.response.emit("GET " + route, {"error": reply.errorString()})
                else:
                    self.response.emit("GET " + route, json.loads(bytes(reply.readAll())))
            except (ValueError, TypeError):
                self.status.emit("读取失败：服务返回了无效数据")
            finally:
                reply.deleteLater()

        reply.finished.connect(completed)

    def close(self):
        self.closing = True
        self.retry.stop()
        self.heartbeat.stop()
        self.avatars.close()
        self.socket.close()
