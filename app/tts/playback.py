from __future__ import annotations

import asyncio
import uuid


class PlaybackReceipts:
    def __init__(self):
        self.renderers = []
        self.pending = {}

    def register(self, client):
        if client not in self.renderers:
            self.renderers.append(client)

    def remove(self, client):
        if client in self.renderers:
            self.renderers.remove(client)
        for identifier, (owner, future) in list(self.pending.items()):
            if owner is client:
                self.finish(client, identifier, "disconnected")

    def prepare(self, clients):
        for renderer in list(self.renderers):
            if renderer not in clients:
                self.remove(renderer)
        identifier = uuid.uuid4().hex
        future = None
        if self.renderers:
            future = asyncio.get_running_loop().create_future()
            self.pending[identifier] = (self.renderers[0], future)
        return identifier, future

    def finish(self, client, identifier, status):
        item = self.pending.get(identifier)
        if item is not None and item[0] is client and not item[1].done():
            item[1].set_result(status)

    def forget(self, identifier):
        item = self.pending.pop(identifier, None)
        if item is not None and not item[1].done():
            item[1].cancel()
