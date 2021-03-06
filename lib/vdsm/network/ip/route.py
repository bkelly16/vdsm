# Copyright 2017 Red Hat, Inc.
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

import abc
from functools import wraps
import logging
import sys

import six

from vdsm.network.ipwrapper import IPRoute2Error
from vdsm.network.ipwrapper import Route
from vdsm.network.ipwrapper import routeAdd
from vdsm.network.ipwrapper import routeDel
from vdsm.network.ipwrapper import routeShowTable


@six.add_metaclass(abc.ABCMeta)
class IPRouteApi(object):

    @staticmethod
    def add(route_data):
        """ Adding a route entry described by an IPRouteData data object """
        raise NotImplementedError

    @staticmethod
    def delete(route_data):
        """ Delete a route entry described by an IPRouteData data object """
        raise NotImplementedError

    @staticmethod
    def routes(table='all'):
        raise NotImplementedError


class IPRouteData(object):
    """ A data structure used to keep route information """

    def __init__(self, to, via, family, src=None, device=None, table=None):
        self._to = to
        self._via = via
        self._family = family
        self._src = src
        self._device = device
        self._table = table

    @property
    def to(self):
        return self._to

    @property
    def via(self):
        return self._via

    @property
    def src(self):
        return self._src

    @property
    def family(self):
        return self._family

    @property
    def device(self):
        return self._device

    @property
    def table(self):
        return self._table


class IPRouteError(Exception):
    pass


class IPRouteDeleteError(IPRouteError):
    pass


def _translate_iproute2_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except IPRoute2Error as e:
            _, value, tb = sys.exc_info()
            error_message = e.args[1][0]
            if 'No such process' in error_message:
                six.reraise(IPRouteDeleteError, value, tb)
            else:
                six.reraise(IPRouteError, value, tb)

    return wrapper


class _Iproute2Route(IPRouteApi):

    @staticmethod
    @_translate_iproute2_exceptions
    def add(route_data):
        r = route_data
        routeAdd(Route(r.to, r.via, r.src, r.device, r.table), r.family)

    @staticmethod
    @_translate_iproute2_exceptions
    def delete(route_data):
        r = route_data
        routeDel(Route(r.to, r.via, r.src, r.device, r.table), r.family)

    @staticmethod
    def routes(table='all'):
        routes_data = routeShowTable(table)
        for route_data in routes_data:
            try:
                r = Route.fromText(route_data)
                family = 6 if _is_ipv6_addr_soft_check(r.network) else 4
                yield IPRouteData(
                    r.network, r.via, family, r.src, r.device, r.table)
            except ValueError:
                logging.warning('Could not parse route %s', route_data)


def _is_ipv6_addr_soft_check(addr):
    return addr.count(':') > 1


IPRoute = _Iproute2Route
