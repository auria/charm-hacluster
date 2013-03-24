#!/usr/bin/python

#
# Copyright 2012 Canonical Ltd.
#
# Authors:
#  Andres Rodriguez <andres.rodriguez@canonical.com>
#

import shutil
import sys
import time
import os
from base64 import b64decode

import maas as MAAS
import lib.utils as utils
import pcmk
import hacluster


def install():
    utils.juju_log('INFO', 'Begin install hook.')
    utils.configure_source()
    utils.install('corosync', 'pacemaker', 'python-netaddr', 'ipmitool')
    # XXX rbd OCF only included with newer versions of ceph-resource-agents.
    # Bundle /w charm until we figure out a better way to install it.
    if not os.path.exists('/usr/lib/ocf/resource.d/ceph'):
        os.makedirs('/usr/lib/ocf/resource.d/ceph')
    if not os.path.isfile('/usr/lib/ocf/resource.d/ceph/rbd'):
        shutil.copy('ocf/ceph/rbd', '/usr/lib/ocf/resource.d/ceph/rbd')
    utils.juju_log('INFO', 'End install hook.')


def get_corosync_conf():
    conf = {}
    for relid in utils.relation_ids('ha'):
        for unit in utils.relation_list(relid):
            conf = {
                'corosync_bindnetaddr':
                    hacluster.get_network_address(
                              utils.relation_get('corosync_bindiface',
                                                 unit, relid)
                              ),
                'corosync_mcastport': utils.relation_get('corosync_mcastport',
                                                         unit, relid),
                'corosync_mcastaddr': utils.config_get('corosync_mcastaddr'),
                'corosync_pcmk_ver': utils.config_get('corosync_pcmk_ver'),
                }
            if None not in conf.itervalues():
                return conf
    missing = [k for k, v in conf.iteritems() if v == None]
    utils.juju_log('INFO',
                   'Missing required principle configuration: %s' % missing)
    return None


def emit_corosync_conf():
    # read config variables
    corosync_conf_context = get_corosync_conf()
    # write config file (/etc/corosync/corosync.conf
    with open('/etc/corosync/corosync.conf', 'w') as corosync_conf:
        corosync_conf.write(utils.render_template('corosync.conf',
                                                  corosync_conf_context))


def emit_base_conf():
    corosync_default_context = {'corosync_enabled': 'yes'}
    # write /etc/default/corosync file
    with open('/etc/default/corosync', 'w') as corosync_default:
        corosync_default.write(utils.render_template('corosync',
                                                     corosync_default_context))
    corosync_key = utils.config_get('corosync_key')
    if corosync_key:
        # write the authkey
        with open('/etc/corosync/authkey', 'w') as corosync_key_file:
            corosync_key_file.write(b64decode(corosync_key))
        os.chmod = ('/etc/corosync/authkey', 0400)


def config_changed():
    utils.juju_log('INFO', 'Begin config-changed hook.')

    corosync_key = utils.config_get('corosync_key')
    if not corosync_key:
        utils.juju_log('CRITICAL',
                       'No Corosync key supplied, cannot proceed')
        sys.exit(1)

    if int(utils.config_get('corosync_pcmk_ver')) == 1:
        hacluster.enable_lsb_services('pacemaker')
    else:
        hacluster.disable_lsb_services('pacemaker')

    # Create a new config file
    emit_base_conf()

    # Reconfigure the cluster if required
    configure_cluster()

    # Setup fencing.
    configure_stonith()

    utils.juju_log('INFO', 'End config-changed hook.')


def upgrade_charm():
    utils.juju_log('INFO', 'Begin upgrade-charm hook.')
    install()
    config_changed()
    utils.juju_log('INFO', 'End upgrade-charm hook.')


def restart_corosync():
    if int(utils.config_get('corosync_pcmk_ver')) == 1:
        if utils.running("pacemaker"):
            utils.stop("pacemaker")
        utils.restart("corosync")
        time.sleep(2)
        utils.start("pacemaker")
    else:
        utils.restart("corosync")

HAMARKER = '/var/lib/juju/haconfigured'


def configure_cluster():
    # Check that we are not already configured
    if os.path.exists(HAMARKER):
        utils.juju_log('INFO',
                       'HA already configured, not reconfiguring')
        return
    # Check that we are related to a principle and that
    # it has already provided the required corosync configuration
    if not get_corosync_conf():
        utils.juju_log('WARNING',
                       'Unable to configure corosync right now, bailing')
        return
    else:
        utils.juju_log('INFO',
                       'Ready to form cluster - informing peers')
        utils.relation_set(ready=True,
                           rid=utils.relation_ids('hanode')[0])
    # Check that there's enough nodes in order to perform the
    # configuration of the HA cluster
    if (len(get_cluster_nodes()) <
        int(utils.config_get('cluster_count'))):
        utils.juju_log('WARNING', 'Not enough nodes in cluster, bailing')
        return

    relids = utils.relation_ids('ha')
    if len(relids) == 1:  # Should only ever be one of these
        # Obtain relation information
        relid = relids[0]
        unit = utils.relation_list(relid)[0]
        utils.juju_log('INFO',
                       'Using rid {} unit {}'.format(relid, unit))
        import ast
        resources = \
            {} if utils.relation_get("resources",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("resources",
                                                        unit, relid))
        resource_params = \
            {} if utils.relation_get("resource_params",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("resource_params",
                                                        unit, relid))
        groups = \
            {} if utils.relation_get("groups",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("groups",
                                                        unit, relid))
        ms = \
            {} if utils.relation_get("ms",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("ms",
                                                        unit, relid))
        orders = \
            {} if utils.relation_get("orders",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("orders",
                                                        unit, relid))
        colocations = \
            {} if utils.relation_get("colocations",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("colocations",
                                                        unit, relid))
        clones = \
            {} if utils.relation_get("clones",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("clones",
                                                        unit, relid))
        init_services = \
            {} if utils.relation_get("init_services",
                                     unit, relid) is None \
               else ast.literal_eval(utils.relation_get("init_services",
                                                        unit, relid))

    else:
        utils.juju_log('WARNING',
                       'Related to {} ha services'.format(len(relids)))
        return

    if True in [ra.startswith('ocf:openstack')
                for ra in resources.itervalues()]:
        utils.install('openstack-resource-agents')
    if True in [ra.startswith('ocf:ceph')
                for ra in resources.itervalues()]:
        utils.install('ceph-resource-agents')

    utils.juju_log('INFO', 'Configuring and restarting corosync')
    emit_corosync_conf()
    restart_corosync()

    utils.juju_log('INFO', 'Waiting for PCMK to start')
    pcmk.wait_for_pcmk()

    utils.juju_log('INFO', 'Doing global cluster configuration')
    cmd = "crm configure property stonith-enabled=false"
    pcmk.commit(cmd)
    cmd = "crm configure property no-quorum-policy=ignore"
    pcmk.commit(cmd)
    cmd = 'crm configure rsc_defaults $id="rsc-options"' \
          ' resource-stickiness="100"'
    pcmk.commit(cmd)

    utils.juju_log('INFO', 'Configuring Resources')
    utils.juju_log('INFO', str(resources))

    for res_name, res_type in resources.iteritems():
        # disable the service we are going to put in HA
        if res_type.split(':')[0] == "lsb":
            hacluster.disable_lsb_services(res_type.split(':')[1])
            if utils.running(res_type.split(':')[1]):
                utils.stop(res_type.split(':')[1])
        elif (len(init_services) != 0 and
              res_name in init_services and
              init_services[res_name]):
            hacluster.disable_upstart_services(init_services[res_name])
            if utils.running(init_services[res_name]):
                utils.stop(init_services[res_name])
        # Put the services in HA, if not already done so
        #if not pcmk.is_resource_present(res_name):
        if not pcmk.crm_opt_exists(res_name):
            if not res_name in resource_params:
                cmd = 'crm -F configure primitive %s %s' % (res_name, res_type)
            else:
                cmd = 'crm -F configure primitive %s %s %s' % \
                            (res_name,
                             res_type,
                             resource_params[res_name])
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    utils.juju_log('INFO', 'Configuring Groups')
    utils.juju_log('INFO', str(groups))
    for grp_name, grp_params in groups.iteritems():
        if not pcmk.crm_opt_exists(grp_name):
            cmd = 'crm -F configure group %s %s' % (grp_name, grp_params)
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    utils.juju_log('INFO', 'Configuring Master/Slave (ms)')
    utils.juju_log('INFO', str(ms))
    for ms_name, ms_params in ms.iteritems():
        if not pcmk.crm_opt_exists(ms_name):
            cmd = 'crm -F configure ms %s %s' % (ms_name, ms_params)
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    utils.juju_log('INFO', 'Configuring Orders')
    utils.juju_log('INFO', str(orders))
    for ord_name, ord_params in orders.iteritems():
        if not pcmk.crm_opt_exists(ord_name):
            cmd = 'crm -F configure order %s %s' % (ord_name, ord_params)
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    utils.juju_log('INFO', 'Configuring Colocations')
    utils.juju_log('INFO', str(colocations))
    for col_name, col_params in colocations.iteritems():
        if not pcmk.crm_opt_exists(col_name):
            cmd = 'crm -F configure colocation %s %s' % (col_name, col_params)
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    utils.juju_log('INFO', 'Configuring Clones')
    utils.juju_log('INFO', str(clones))
    for cln_name, cln_params in clones.iteritems():
        if not pcmk.crm_opt_exists(cln_name):
            cmd = 'crm -F configure clone %s %s' % (cln_name, cln_params)
            pcmk.commit(cmd)
            utils.juju_log('INFO', '%s' % cmd)

    for res_name, res_type in resources.iteritems():
        if len(init_services) != 0 and res_name in init_services:
            # Checks that the resources are running and started.
            # Ensure that clones are excluded as the resource is
            # not directly controllable (dealt with below)
            # Ensure that groups are cleaned up as a whole rather
            # than as individual resources.
            if (res_name not in clones.values() and
                res_name not in groups.values() and
                not pcmk.crm_res_running(res_name)):
                # Just in case, cleanup the resources to ensure they get
                # started in case they failed for some unrelated reason.
                cmd = 'crm resource cleanup %s' % res_name
                pcmk.commit(cmd)

    for cl_name in clones:
        # Always cleanup clones
        cmd = 'crm resource cleanup %s' % cl_name
        pcmk.commit(cmd)

    for grp_name in groups:
        # Always cleanup groups
        cmd = 'crm resource cleanup %s' % grp_name
        pcmk.commit(cmd)

    for rel_id in utils.relation_ids('ha'):
        utils.relation_set(rid=rel_id,
                           clustered="yes")

    with open(HAMARKER, 'w') as marker:
        marker.write('done')

    configure_stonith()


def configure_stonith():
    if utils.config_get('stonith_enabled') not in ['true', 'True']:
        return

    if not os.path.exists(HAMARKER):
        utils.juju_log('INFO',
                       'HA not yet configured, skipping STONITH config.')
        return

    utils.juju_log('INFO', 'Configuring STONITH for all nodes in cluster.')
    # configure stontih resources for all nodes in cluster.
    # note: this is totally provider dependent and requires
    # access to the MAAS API endpoint, using endpoint and credentials
    # set in config.
    url = utils.config_get('maas_url')
    creds = utils.config_get('maas_credentials')
    if None in [url, creds]:
        utils.juju_log('ERROR', 'maas_url and maas_credentials must be set'\
                       ' in config to enable STONITH.')
        sys.exit(1)

    maas = MAAS.MAASHelper(url, creds)
    nodes = maas.list_nodes()
    if not nodes:
        utils.juju_log('ERROR', 'Could not obtain node inventory from '\
                       'MAAS @ %s.' % url)
        sys.exit(1)

    cluster_nodes = pcmk.list_nodes()
    for node in cluster_nodes:
        rsc, constraint = pcmk.maas_stonith_primitive(nodes, node)
        if not rsc:
            utils.juju_log('ERROR',
                           'Failed to determine STONITH primitive for node'\
                           ' %s' % node)
            sys.exit(1)

        rsc_name = str(rsc).split(' ')[1]
        if not pcmk.is_resource_present(rsc_name):
            utils.juju_log('INFO', 'Creating new STONITH primitive %s.' %\
                           rsc_name)
            cmd = 'crm -F configure %s' % rsc
            pcmk.commit(cmd)
            if constraint:
                cmd = 'crm -F configure %s' % constraint
                pcmk.commit(cmd)
        else:
            utils.juju_log('INFO', 'STONITH primitive already exists '\
                           'for node.')

    cmd = "crm configure property stonith-enabled=true"
    pcmk.commit(cmd)


def get_cluster_nodes():
    hosts = []
    hosts.append(utils.unit_get('private-address'))
    for relid in utils.relation_ids('hanode'):
        for unit in utils.relation_list(relid):
            if utils.relation_get('ready',
                                  rid=relid,
                                  unit=unit):
                hosts.append(utils.relation_get('private-address',
                                                unit, relid))
    hosts.sort()
    return hosts


hooks = {
    'install': install,
    'config-changed': config_changed,
    'upgrade-charm': upgrade_charm,
    'ha-relation-joined': configure_cluster,
    'ha-relation-changed': configure_cluster,
    'hanode-relation-joined': configure_cluster,
    'hanode-relation-changed': configure_cluster,
    }

utils.do_hooks(hooks)
