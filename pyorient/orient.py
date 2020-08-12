#  Copyright 2020 Niko Usai <usai.niko@gmail.com>, http://mogui.it; Marc Auberer, https://marc-auberer.de
#
#  this file is part of pyorient
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#   limitations under the License.

__author__ = 'mogui <mogui83@gmail.com>, Marc Auberer <marc.auberer@sap.com>'

# Python imports
import socket
import struct
import select

# Local imports
from .serializations import OrientSerialization
from .utils import dlog
from .constants import SOCK_CONN_TIMEOUT, FIELD_SHORT, SUPPORTED_PROTOCOL, ERROR_ON_NEWER_PROTOCOL
from .exceptions import PyOrientConnectionPoolException, PyOrientWrongProtocolVersionException,\
    PyOrientConnectionException


class OrientSocket(object):
    """
    Class representing the binary connection to the database, it does all the low level communication and holds information on server version and cluster map
    .. DANGER:: Should not be used directly
    :param host: hostname of the server to connect
    :param port: integer port of the server
    """
    def __init__(self, host, port, serialization_type=OrientSerialization.CSV):
        # Initialize attributes with default values
        self.connected = False
        self.host = host
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.protocol = -1
        self.session_id = -1
        self.db_opened = None
        self.serialization_type = serialization_type
        self.in_transaction = False
        self._props = None

    def get_connection(self):
        # Establish the socket connection and return the connected socket
        if not self.connected:
            self.connect()
        return self._socket

    def connect(self):
        """
        Connects to the database server
        could raise :class:`PyOrientConnectionPoolException`
        """
        dlog("Trying to connect ...")
        try:
            # Set timeout and connect socket to the provided host and port
            self._socket.settimeout(SOCK_CONN_TIMEOUT)
            self._socket.connect((self.host, self.port))

            # Read short value from server to check, if the server is working correctly
            _answer = self._socket.recv(FIELD_SHORT['bytes'])
            if len(_answer) != 2:  # A short is 2 bytes long
                # Close the socket and throw exception if the server is not working correctly
                self._socket.close()
                raise PyOrientConnectionPoolException("Server sent empty string", [])

            # Unpack protocol version
            self.protocol = struct.unpack('!h', _answer)[0]

            # Raise exception on higher protocol version than supported, if enabled
            if self.protocol > SUPPORTED_PROTOCOL and ERROR_ON_NEWER_PROTOCOL:
                raise PyOrientWrongProtocolVersionException("Protocol version " + str(self.protocol) + " is not "
                      "supported by this client version. Please check, if there's a new pyorient version available", [])

            self.connected = True
        except socket.error as e:
            # Catch the exception and raise it up as a PyOrientConnectionException
            self.connected = False
            raise PyOrientConnectionException("Socket error: %s" % e, [])

    def close(self):
        """
        Close the connection to the database server
        """
        # Stop connection
        self._socket.close()
        self.connected = False
        # Reset all attributes to default
        self.host = ''
        self.port = 0
        self.protocol = -1
        self.session_id = -1

    def detect_server_disconnect(self, server_conn_timeout=SOCK_CONN_TIMEOUT):
        # Trick to detect server disconnection to prevent this:
        # https://docs.python.org/2/howto/sockets.html#when-sockets-die
        try:
            # As soon as the connection crashes, this raises an exception
            return select.select([], [self._socket], [self._socket], server_conn_timeout)
        except select.error as e:
            # Connection crash, try to shutdown connection gracefully
            self.connected = False
            self._socket.close()
            raise e

    def write(self, buff):
        # Call method to detect server disconnect
        _, ready_to_write, in_error = self.detect_server_disconnect(1)

        if not in_error and ready_to_write:
            # Socket works -> send all data
            self._socket.sendall(buff)
            return len(buff)
        else:
            # Socket does not work -> close and raise exception
            self.connected = False
            self._socket.close()
            raise PyOrientConnectionException("Socket error", [])

    def read(self, _len_to_read):
        while True:
            # Call method to detect server disconnect
            ready_to_read, _, in_error = self.detect_server_disconnect()

            if len(ready_to_read) > 0:
                buf = bytearray(_len_to_read)
                view = memoryview(buf)
                while _len_to_read:
                    n_bytes = self._socket.recv_into(view, _len_to_read)
                    # Nothing read -> Server went down
                    if not n_bytes:
                        self._socket.close()
                        # TODO: Implement re-connection to another listener
                        raise PyOrientConnectionException("Server seems to went down", [])

                    # Shorten view and _len_to_read by n_bytes
                    view = view[n_bytes:]
                    _len_to_read -= n_bytes
                # Read successfully, return result
                return bytes(buf)

            # Close connection, if error(s) occurred
            if len(in_error) > 0:
                self._socket.close()
                raise PyOrientConnectionException("Socket error", [])

