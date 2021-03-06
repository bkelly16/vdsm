#
# Copyright 2012-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from errno import EINTR
import json
import httplib
import logging
import threading
import re

from vdsm import concurrent
from vdsm import xmlrpc
from vdsm.common.define import doneCode
import API


class BindingXMLRPC(object):
    def __init__(self, cif, log):
        self.cif = cif
        self.log = log

        self._enabled = False
        self.server = self._createXMLRPCServer()

    def start(self):
        """
        Serve clients until stopped
        """
        def threaded_start():
            self.log.info("XMLRPC server running")
            self.server.timeout = 1
            self._enabled = True

            while self._enabled:
                try:
                    self.server.handle_request()
                except Exception as e:
                    if e[0] != EINTR:
                        self.log.error("xml-rpc handler exception",
                                       exc_info=True)
            self.log.info("XMLRPC server stopped")

        self._thread = concurrent.thread(threaded_start, name='BindingXMLRPC',
                                         log=self.log)
        self._thread.start()

    def add_socket(self, connected_socket, socket_address):
        self.server.add(connected_socket, socket_address)

    def stop(self):
        self.log.info("Stopping XMLRPC server")
        self._enabled = False
        self.server.server_close()
        self._thread.join()
        return {'status': doneCode}

    def _createXMLRPCServer(self):
        """
        Create xml-rpc server over http
        """

        threadLocal = self.cif.threadLocal

        class RequestHandler(xmlrpc.IPXMLRPCRequestHandler):

            # Timeout for the request socket
            timeout = 60
            log = logging.getLogger("BindingXMLRPC.RequestHandler")

            HEADER_POOL = 'Storage-Pool-Id'
            HEADER_DOMAIN = 'Storage-Domain-Id'
            HEADER_IMAGE = 'Image-Id'
            HEADER_VOLUME = 'Volume-Id'
            HEADER_TASK_ID = 'Task-Id'
            HEADER_RANGE = 'Range'
            HEADER_CONTENT_LENGTH = 'content-length'
            HEADER_CONTENT_TYPE = 'content-type'
            HEADER_CONTENT_RANGE = 'content-range'

            class RequestException(Exception):
                def __init__(self, httpStatusCode, errorMessage):
                    self.httpStatusCode = httpStatusCode
                    self.errorMessage = errorMessage

            def setup(self):
                threadLocal.client = self.client_address[0]
                threadLocal.server = self.request.getsockname()[0]
                return xmlrpc.IPXMLRPCRequestHandler.setup(self)

            def do_GET(self):
                try:
                    length = self._getLength()
                    img = self._createImage()
                    startEvent = threading.Event()
                    methodArgs = {'fileObj': self.wfile,
                                  'length': length}

                    uploadFinishedEvent, operationEndCallback = \
                        self._createEventWithCallback()

                    # Optional header
                    volUUID = self.headers.getheader(self.HEADER_VOLUME)

                    response = img.uploadToStream(methodArgs,
                                                  operationEndCallback,
                                                  startEvent, volUUID)

                    if response['status']['code'] == 0:
                        self.send_response(httplib.PARTIAL_CONTENT)
                        self.send_header(self.HEADER_CONTENT_TYPE,
                                         'application/octet-stream')
                        self.send_header(self.HEADER_CONTENT_LENGTH, length)
                        self.send_header(self.HEADER_CONTENT_RANGE,
                                         "bytes 0-%d" % (length - 1))
                        self.send_header(self.HEADER_TASK_ID, response['uuid'])
                        self.end_headers()
                        startEvent.set()
                        self._waitForEvent(uploadFinishedEvent)
                    else:
                        self._send_error_response(response)

                except self.RequestException as e:
                    # This is an expected exception, so traceback is unneeded
                    self.send_error(e.httpStatusCode, e.errorMessage)
                except Exception:
                    self.send_error(httplib.INTERNAL_SERVER_ERROR,
                                    "error during execution",
                                    exc_info=True)

            def do_PUT(self):
                try:
                    contentLength = self._getIntHeader(
                        self.HEADER_CONTENT_LENGTH,
                        httplib.LENGTH_REQUIRED)

                    img = self._createImage()

                    methodArgs = {'fileObj': self.rfile,
                                  'length': contentLength}

                    uploadFinishedEvent, operationEndCallback = \
                        self._createEventWithCallback()

                    # Optional header
                    volUUID = self.headers.getheader(self.HEADER_VOLUME)

                    response = img.downloadFromStream(methodArgs,
                                                      operationEndCallback,
                                                      volUUID)

                    if response['status']['code'] == 0:
                        while not uploadFinishedEvent.is_set():
                            uploadFinishedEvent.wait()
                        self.send_response(httplib.OK)
                        self.send_header(self.HEADER_TASK_ID, response['uuid'])
                        self.end_headers()
                    else:
                        self._send_error_response(response)

                except self.RequestException as e:
                    self.send_error(e.httpStatusCode, e.errorMessage)
                except Exception:
                    self.send_error(httplib.INTERNAL_SERVER_ERROR,
                                    "error during execution",
                                    exc_info=True)

            def _createImage(self):
                # Required headers
                spUUID = self.headers.getheader(self.HEADER_POOL)
                sdUUID = self.headers.getheader(self.HEADER_DOMAIN)
                imgUUID = self.headers.getheader(self.HEADER_IMAGE)
                if not all((spUUID, sdUUID, imgUUID)):
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "missing or empty required header(s):"
                        " spUUID=%s sdUUID=%s imgUUID=%s"
                        % (spUUID, sdUUID, imgUUID))

                return API.Image(imgUUID, spUUID, sdUUID)

            @staticmethod
            def _createEventWithCallback():
                operationFinishedEvent = threading.Event()

                def setCallback():
                    operationFinishedEvent.set()

                return operationFinishedEvent, setCallback

            @staticmethod
            def _waitForEvent(event):
                while not event.is_set():
                    event.wait()

            def _getIntHeader(self, headerName, missingError):
                value = self._getRequiredHeader(headerName, missingError)

                return self._getInt(value)

            def _getRequiredHeader(self, headerName, missingError):
                value = self.headers.getheader(
                    headerName)
                if not value:
                    raise self.RequestException(
                        missingError,
                        "missing header %s" % headerName)
                return value

            def _getInt(self, value):
                try:
                    return int(value)
                except ValueError:
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "not int value %r" % value)

            def _getLength(self):
                value = self._getRequiredHeader(self.HEADER_RANGE,
                                                httplib.BAD_REQUEST)

                m = re.match(r'^bytes=0-(\d+)$', value)
                if m is None:
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "Unsupported range: %r , expected: bytes=0-last_byte" %
                        value)

                last_byte = m.group(1)
                return self._getInt(last_byte) + 1

            def send_error(self, error, message, exc_info=False):
                try:
                    self.log.error(message, exc_info=exc_info)
                    self.send_response(error)
                    self.end_headers()
                except Exception:
                    self.log.error("failed to return response",
                                   exc_info=True)

            def _send_error_response(self, response):
                self.send_response(httplib.INTERNAL_SERVER_ERROR)
                json_response = json.dumps(response)
                self.send_header(self.HEADER_CONTENT_TYPE,
                                 'application/json')
                self.send_header(self.HEADER_CONTENT_LENGTH,
                                 len(json_response))
                self.end_headers()
                self.wfile.write(json_response)

        server = xmlrpc.SimpleThreadedXMLRPCServer(
            requestHandler=RequestHandler,
            logRequests=False)

        return server


class XmlDetector():
    log = logging.getLogger("XmlDetector")
    NAME = "xml"
    REQUIRED_SIZE = 6

    def __init__(self, xml_binding):
        self.xml_binding = xml_binding

    def detect(self, data):
        return data.startswith("PUT /") or data.startswith("GET /")

    def handle_socket(self, client_socket, socket_address):
        self.xml_binding.add_socket(client_socket, socket_address)
        self.log.debug("http detected from %s", socket_address)
