#
# aionn - asyncio messaging library based on nanomsg and nnpy
#
# Copyright (C) 2016 by Artur Wroblewski <wrobell@riseup.net>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import asyncio
import logging
import nnpy

from nnpy.socket import ffi, nanomsg
from nnpy.errors import convert as error_convert

logger = logging.getLogger(__name__)

EAGAIN = 11
ENOPROTOOPT = 92

NN_MSG = ffi.cast('size_t', -1)

class Socket(nnpy.Socket):
    def __init__(self, domain, protocol, loop=None):
        super().__init__(domain, protocol)
        if loop == None:
            loop = asyncio.get_event_loop()
        self._loop = loop

        self._reader = asyncio.Queue(loop=loop)
        self._fd_reader = None
        self._read_flags = 0

        self._writer = asyncio.Event(loop=loop)
        self._fd_writer = None
        self._write_flags = 0

        self._data = None

    def bind(self, addr):
        super().bind(addr)
        self._enable_writer()
        self._enable_reader()

    def connect(self, addr):
        super().connect(addr)
        self._enable_writer()
        self._enable_reader()

    async def recv(self, flags=0):
        """
        Receive data from the socket.

        The method is a coroutine.

        The data is always received with `NN_DONTWAIT` flag enabled.
        Therefore the `flags` parameter is ignored in practice at the
        moment.

        :param flags: Receiving data flags.

        .. seealso:: `ffi.from_buffer <http://cffi.readthedocs.io/en/latest/ref.html#ffi-buffer-ffi-from-buffer>`_
        .. seealso:: `nn_send <http://nanomsg.org/v1.0.0/nn_recv.3.html>`_
        """
        self._read_flags = flags | nnpy.DONTWAIT
        value = await self._reader.get()
        return value

    async def send(self, data, flags=0):
        """
        Send data to the socket.

        The method is a coroutine.

        The `data` is buffer object - bytes or bytearray.

        The data is always sent with `NN_DONTWAIT` flag enabled. Therefore
        the `flags` parameter is ignored in practice at the moment.

        :param data: Data to be sent.
        :param flags: Sending data flags.

        .. seealso:: `ffi.from_buffer <http://cffi.readthedocs.io/en/latest/ref.html#ffi-buffer-ffi-from-buffer>`_
        .. seealso:: `nn_send <http://nanomsg.org/v1.0.0/nn_send.3.html>`_
        """
        self._data = data
        self._write_flags = flags | nnpy.DONTWAIT
        await self._writer.wait()

    def _notify_recv(self):
        data = ffi.new('char**')
        rc = nanomsg.nn_recv(self.sock, data, NN_MSG, self._read_flags)
        # TODO: EAGAIN
        error_convert(rc)
        self._reader.put_nowait(ffi.buffer(data[0], rc)[:])
        nanomsg.nn_freemsg(data[0])

    def _notify_send(self):
        if self._data is not None:
            data = ffi.from_buffer(self._data)
            rc = nanomsg.nn_send(self.sock, data, len(data), self._write_flags)
            if rc < 0 and nanomsg.nn_errno() == EAGAIN:
                # avoid blocking sender by delaying next write; in the
                # future, use NN_SNDTIMEO and allow the operation to
                # timeout; at the moment we use default timeout approach
                # (infinite timeout), see nn_setsockopt(3)/NN_SNDTIMEO
                if __debug__:
                    logger.debug('EAGAIN on send, delay sender')
                self._loop.remove_writer(self._fd_writer)
                self._loop.call_later(1, self._enable_writer)
            else:
                error_convert(rc, rc)
                self._writer.set()
                self._writer.clear()
                self._data = None

    def _enable_reader(self):
        try:
            self._fd_reader = self.getsockopt(nnpy.SOL_SOCKET, nnpy.RCVFD)
            self._loop.add_reader(self._fd_reader, self._notify_recv)
        except nnpy.NNError as ex:
            if ex.error_no != ENOPROTOOPT:
                raise

    def _enable_writer(self):
        try:
            self._fd_writer = self.getsockopt(nnpy.SOL_SOCKET, nnpy.SNDFD)
            self._loop.add_writer(self._fd_writer, self._notify_send)
        except nnpy.NNError as ex:
            if ex.error_no != ENOPROTOOPT:
                raise

# vim: sw=4:et:ai
