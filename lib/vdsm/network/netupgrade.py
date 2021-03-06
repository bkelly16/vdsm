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

import string

import six

from vdsm.config import config
from vdsm.constants import LEGACY_MANAGEMENT_NETWORKS

from vdsm.virt import libvirtnetwork

from vdsm.network.canonicalize import canonicalize_bondings
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.configurators.ifcfg import ConfigWriter
from vdsm.network.configurators.ifcfg import Ifcfg
from vdsm.network.netconfpersistence import RunningConfig, PersistentConfig
from vdsm.network.netswitch import netinfo
from vdsm.network.netinfo.cache import NetInfo, libvirt_vdsm_nets
from vdsm.network.kernelconfig import KernelConfig


def upgrade():
    rconfig = RunningConfig()
    pconfig = PersistentConfig()

    libvirt_networks = libvirtnetwork.networks()

    _upgrade_volatile_running_config(rconfig)

    if rconfig.config_exists() or pconfig.config_exists():
        _upgrade_unified_configuration(rconfig)
        _upgrade_unified_configuration(pconfig)
    else:
        # In case unified config has not existed before, it is assumed that
        # the networks existance have been persisted in libvirt db.
        vdsmnets = libvirt_vdsm_nets(libvirt_networks)
        _create_unified_configuration(rconfig, NetInfo(netinfo(vdsmnets)))

    _cleanup_libvirt_networks(libvirt_networks)


def _upgrade_volatile_running_config(rconfig):
    """
    Relocate the volatile version of running config (if exists)
    to the persisted version.
    This procedure is required in order to support upgrades to the new
    persisted version of running config.
    """
    if not rconfig.config_exists():
        volatile_rconfig = RunningConfig(volatile=True)
        if volatile_rconfig.config_exists():
            rconfig.networks = volatile_rconfig.networks
            rconfig.bonds = volatile_rconfig.bonds
            rconfig.save()
            volatile_rconfig.delete()


def _upgrade_unified_configuration(config):
    """
    Process an unified configuration file and normalize it to an up do date
    format.
    """
    if config.networks:
        _normalize_net_address(config.networks)
        _normalize_net_ifcfg_keys(config.networks)

        canonicalize_networks(config.networks)
        canonicalize_bondings(config.bonds)

        config.save()


def _normalize_net_address(networks):
    for net_name, net_attr in six.viewitems(networks):
        if 'defaultRoute' not in net_attr:
            net_attr['defaultRoute'] = net_name in LEGACY_MANAGEMENT_NETWORKS


def _normalize_net_ifcfg_keys(networks):
    """
    Ignore keys in persisted networks that might originate from vdsm-reg.
    these might be a result of calling setupNetworks with ifcfg values
    that come from the original interface that is serving the management
    network. for 3.5, VDSM still supports passing arbitrary values
    directly to the ifcfg files, e.g. 'IPV6_AUTOCONF=no'.
    We filter them out here since they are not supported anymore.
    """
    for netname, netattrs in six.viewitems(networks):
        networks[netname] = {k: v for k, v in six.viewitems(netattrs)
                             if not _is_unsupported_ifcfg_key(k)}


def _is_unsupported_ifcfg_key(key):
    return set(key) <= set(string.ascii_uppercase + string.digits + '_')


def _create_unified_configuration(rconfig, net_info):
    """
    Given netinfo report, generate unified configuration and persist it.

    Networks and Bonds detected by the network caps/netinfo are recorded.
    In case there are external bonds (not owned), they are still counted for in
    this regard.

    Note: Unified persistence mode is not activated in this context, it is left
    as a separate step/action.
    """
    kconfig = KernelConfig(net_info)

    rconfig.networks = kconfig.networks
    rconfig.bonds = _filter_owned_bonds(kconfig.bonds)

    rconfig.save()
    ConfigWriter.clearBackups()
    RunningConfig.store()


def _filter_owned_bonds(kconfig_bonds):
    """
    Bonds retrieved from the kernel include both the ones owned by us and the
    ones that are not.
    The filter returns only the owned bonds by examining the ifcfg files
    in case ifcfg persistence is set.
    """
    if config.get('vars', 'net_persistence') == 'ifcfg':
        return {bond_name: bond_attrs
                for bond_name, bond_attrs in six.viewitems(kconfig_bonds)
                if Ifcfg.owned_device(bond_name)}
    return kconfig_bonds


def _cleanup_libvirt_networks(libvirt_networks):
    """
    Host networks are no longer persisted in libvirt db, therefore, they are
    removed as part of the upgrade.
    Note: The role of managing libvirt networks has passed to virt.
    """
    for net in libvirt_networks:
        libvirtnetwork.removeNetwork(net)
