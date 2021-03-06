# Copyright 2016 Red Hat, Inc.
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

import dbus

from . import NMDbus


class NMDbusSettings(object):
    NM_SETTINGS_PATH = '/org/freedesktop/NetworkManager/Settings'
    NM_SETTINGS_IF_NAME = 'org.freedesktop.NetworkManager.Settings'

    def __init__(self):
        set_proxy = NMDbus.bus.get_object(NMDbus.NM_IF_NAME,
                                          self.NM_SETTINGS_PATH)
        self._settings = dbus.Interface(set_proxy, self.NM_SETTINGS_IF_NAME)

    def connections(self):
        return (_NMDbusConnectionSettings(con_path)
                for con_path in self._settings.ListConnections())

    def connection(self, connection_path):
        return _NMDbusConnectionSettings(connection_path)


class _NMDbusConnectionSettings(object):
    NM_SETTINGS_CON_IF_NAME = (
        'org.freedesktop.NetworkManager.Settings.Connection')

    def __init__(self, connection_path):
        con_proxy = NMDbus.bus.get_object(NMDbus.NM_IF_NAME, connection_path)
        con_settings = dbus.Interface(con_proxy, self.NM_SETTINGS_CON_IF_NAME)
        self._con_settings = con_settings
        self._config = con_settings.GetSettings()

    def delete(self):
        self._con_settings.Delete()

    @property
    def connection(self):
        return _SettingConnection(self._config)

    @property
    def ethernet(self):
        if _Setting8023Ethernet.GROUP in self._config:
            return _Setting8023Ethernet(self._config)

        raise AttributeError('"{}" object has no attribute "{}"'.format(
            type(self).__name__, 'ethernet'))

    @property
    def ipv4(self):
        return _SettingIP(self._config, _SettingIP.GROUP_IPV4)

    @property
    def ipv6(self):
        return _SettingIP(self._config, _SettingIP.GROUP_IPV6)


class _Setting8023Ethernet(object):
    GROUP = '802-3-ethernet'

    def __init__(self, config):
        self._config = config[_Setting8023Ethernet.GROUP]

    @property
    def mac_address(self):
        return self._config['mac-address']

    @property
    def mac_address_blacklist(self):
        return self._config['mac-address-blacklist']


class _SettingConnection(object):
    GROUP = 'connection'

    def __init__(self, config):
        self._config = config[_SettingConnection.GROUP]

    @property
    def id(self):
        return self._config['id']

    @property
    def uuid(self):
        return self._config['uuid']

    @property
    def type(self):
        return self._config['type']


class _SettingIP(object):
    GROUP_IPV4 = 'ipv4'
    GROUP_IPV6 = 'ipv6'

    def __init__(self, config, ip_ver_group):
        if ip_ver_group not in (_SettingIP.GROUP_IPV4, _SettingIP.GROUP_IPV6):
            raise NMDbusConnectionSettingsGroupError('No such IP group:'
                                                     '{}'.format(ip_ver_group))
        self._config = config[ip_ver_group]

    @property
    def method(self):
        return self._config['method']

    @property
    def addresses(self):
        return self._config['addresses']

    @property
    def address_data(self):
        return self._config['address-data']

    @property
    def routes(self):
        return self._config['routes']

    @property
    def routes_data(self):
        return self._config['route-data']

    @property
    def dns(self):
        return self._config['dns']

    @property
    def dns_search(self):
        return self._config['dns-search']


class NMDbusConnectionSettingsGroupError(Exception):
    pass
