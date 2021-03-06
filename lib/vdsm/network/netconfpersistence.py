#
# Copyright 2013-2017 Red Hat, Inc.
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
from copy import deepcopy
import errno
import json
import logging
import os
import shutil

import six

from vdsm import constants
from vdsm import utils
from . import errors as ne

CONF_VOLATILE_RUN_DIR = constants.P_VDSM_RUN + 'netconf/'
CONF_RUN_DIR = constants.P_VDSM_LIB + 'staging/netconf/'
CONF_PERSIST_DIR = constants.P_VDSM_LIB + 'persistence/netconf/'

VOLATILE_NET_ATTRS = ('blockingdhcp',)


class BaseConfig(object):
    def __init__(self, networks, bonds):
        self.networks = networks
        self.bonds = bonds

    def setNetwork(self, network, attrs):
        cleanAttrs = BaseConfig._filter_out_net_attrs(attrs)
        self.networks[network] = cleanAttrs
        logging.info('Adding network %s(%s)', network, cleanAttrs)

    def removeNetwork(self, network):
        try:
            del self.networks[network]
            logging.info('Removing network %s', network)
        except KeyError:
            logging.debug('Network %s not found for removal', network)

    def setBonding(self, bonding, attributes):
        self.bonds[bonding] = attributes
        logging.info('Adding %s(%s)', bonding, attributes)

    def removeBonding(self, bonding):
        try:
            del self.bonds[bonding]
            logging.info('Removing %s', bonding)
        except KeyError:
            logging.debug('%s not found for removal', bonding)

    def diffFrom(self, other):
        """Returns a diff Config that shows the what should be changed for
        going from other to self."""
        diff = BaseConfig(self._confDictDiff(self.networks, other.networks),
                          self._confDictDiff(self.bonds, other.bonds))
        return diff

    def __eq__(self, other):
        return self.networks == other.networks and self.bonds == other.bonds

    def __repr__(self):
        return '%s(%s, %s)' % (self.__class__.__name__, self.networks,
                               self.bonds)

    def __bool__(self):
        return True if self.networks or self.bonds else False

    def __nonzero__(self):  # TODO: drop when py2 is no longer needed
        return self.__bool__()

    @staticmethod
    def _confDictDiff(lhs, rhs):
        result = {}
        for name in rhs:
            if name not in lhs:
                result[name] = {'remove': True}

        for name, attr in six.iteritems(lhs):
            if name not in rhs or attr != rhs[name]:
                result[name] = lhs[name]
        return result

    def as_unicode(self):
        return {'networks': json.loads(json.dumps(self.networks)),
                'bonds': json.loads(json.dumps(self.bonds))}

    @staticmethod
    def _filter_out_net_attrs(netattrs):
        attrs = {key: value for key, value in six.viewitems(netattrs)
                 if value is not None}

        _filter_out_volatile_net_attrs(attrs)
        return attrs


class Config(BaseConfig):
    def __init__(self, savePath):
        self.networksPath = os.path.join(savePath, 'nets', '')
        self.bondingsPath = os.path.join(savePath, 'bonds', '')
        nets = self._getConfigs(self.networksPath)
        for net_attrs in six.viewvalues(nets):
            _filter_out_volatile_net_attrs(net_attrs)
        bonds = self._getConfigs(self.bondingsPath)
        super(Config, self).__init__(nets, bonds)

    def delete(self):
        self.networks = {}
        self.bonds = {}
        self._clearDisk()

    def save(self):
        self._clearConfigs()
        for bond, attrs in six.iteritems(self.bonds):
            self._setConfig(attrs, self._bondingPath(bond))
        for network, attrs in six.iteritems(self.networks):
            self._setConfig(attrs, self._networkPath(network))
        logging.info('Saved new config %r to %s and %s' %
                     (self, self.networksPath, self.bondingsPath))

    def config_exists(self):
        return (os.path.exists(self.networksPath) and
                os.path.exists(self.bondingsPath))

    def _networkPath(self, network):
        return self.networksPath + network

    def _bondingPath(self, bonding):
        return self.bondingsPath + bonding

    @staticmethod
    def _getConfigDict(path):
        try:
            with open(path, 'r') as configurationFile:
                return json.load(configurationFile)
        except IOError as ioe:
            if ioe.errno == os.errno.ENOENT:
                logging.debug('Network entity at %s not found', path)
                return {}
            else:
                raise

    @staticmethod
    def _getConfigs(path):
        if not os.path.exists(path):
            return {}

        networkEntities = {}

        for fileName in os.listdir(path):
            fullPath = path + fileName
            networkEntities[fileName] = Config._getConfigDict(fullPath)

        return networkEntities

    @staticmethod
    def _setConfig(config, path):
        dirPath = os.path.dirname(path)
        try:
            os.makedirs(dirPath)
        except OSError as ose:
            if errno.EEXIST != ose.errno:
                raise
        with open(path, 'w') as configurationFile:
            json.dump(config, configurationFile, indent=4)

    def _clearConfigs(self):
        self._clearDisk()
        os.makedirs(self.networksPath)
        os.makedirs(self.bondingsPath)

    def _clearDisk(self):
        logging.info('Clearing %s and %s',
                     self.networksPath, self.bondingsPath)
        utils.rmTree(self.networksPath)
        utils.rmTree(self.bondingsPath)


class RunningConfig(Config):
    def __init__(self, volatile=False):
        conf_dir = CONF_VOLATILE_RUN_DIR if volatile else CONF_RUN_DIR
        super(RunningConfig, self).__init__(conf_dir)

    @staticmethod
    def store():
        _store_net_config()


class PersistentConfig(Config):
    def __init__(self):
        super(PersistentConfig, self).__init__(CONF_PERSIST_DIR)


class Transaction(object):
    def __init__(self, config=None, persistent=True, in_rollback=False):
        self.config = config if config is not None else RunningConfig()
        self.base_config = deepcopy(self.config)
        self.persistent = persistent
        self.in_rollback = in_rollback

    def __enter__(self):
        return self.config

    def __exit__(self, ex_type, ex_value, ex_traceback):
        if ex_type is None:
            if self.persistent:
                self.config.save()
        elif self.in_rollback:
            logging.error(
                'Failed rollback transaction to last known good network.',
                exc_info=(ex_type, ex_value, ex_traceback))
        else:
            config_diff = self.base_config.diffFrom(self.config)
            if config_diff:
                logging.warning(
                    'Failed setup transaction,'
                    'reverting to last known good network.',
                    exc_info=(ex_type, ex_value, ex_traceback))
                raise ne.RollbackIncomplete(config_diff, ex_type, ex_value)


def configuredPorts(nets, bridge):
    """Return the configured ports for the bridge"""
    if bridge not in nets:
        return []

    network = nets[bridge]
    nic = network.get('nic')
    bond = network.get('bonding')
    vlan = str(network.get('vlan', ''))
    if bond:
        return [bond + vlan]
    elif nic:
        return [nic + vlan]
    else:  # isolated bridged network
        return []


def _filter_out_volatile_net_attrs(net_attrs):
    for attr in VOLATILE_NET_ATTRS:
        net_attrs.pop(attr, None)


def _store_net_config():
    """
    Declare the current running config as 'safe' and persist this safe config.

    It is implemented by copying the running config to the persistent (safe)
    config in an atomic manner.
    It applies atomic directory copy by using the atomicity of overwriting a
    link (rename syscall).
    """
    safeconf_dir = CONF_PERSIST_DIR[:-1]
    rand_suffix = utils.random_iface_name(max_length=8)
    new_safeconf_dir = safeconf_dir + '.' + rand_suffix
    new_safeconf_symlink = new_safeconf_dir + '.ln'

    shutil.copytree(CONF_RUN_DIR[:-1], new_safeconf_dir)
    os.symlink(new_safeconf_dir, new_safeconf_symlink)

    real_old_safeconf_dir = os.path.realpath(safeconf_dir)
    os.rename(new_safeconf_symlink, safeconf_dir)
    real_old_safeconf_dir_existed = real_old_safeconf_dir != safeconf_dir
    if real_old_safeconf_dir_existed:
        utils.rmTree(real_old_safeconf_dir)
