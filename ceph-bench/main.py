#!/usr/bin/env python3

# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import asyncio
import os
import sys
import tempfile
import yaml

import zaza
from zaza.charm_lifecycle.deploy import deploy as juju_deploy
from zaza.controller import add_model
import zaza.model as model


DEFAULT_APPS = {
    'ceph-mon': {'charm': 'ch:ceph-mon', 'num_units': 3,
                 'options': {'monitor-count': 3},
                 'to': ['0', '1', '2']},
    'ceph-radosgw': {'charm': 'ch:ceph-radosgw', 'num_units': 1,
                     'to': ['3']},
    'vault-mysql-router': {'charm': 'ch:mysql-router'},
    'mysql-innodb-cluster': {'charm': 'ch:mysql-innodb-cluster',
                             'num_units': 3, 'to': ['4', '5', '6']},
    'vault': {'charm': 'ch:vault', 'num_units': 1, 'to': ['7']},
    'woodpecker': {'num_units': 1, 'to': ['8']}
}


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--model',
                        help='Model to deploy to')
    parser.add_argument('-W', '--woodpecker',
                        help='Path to woodpecker charm',
                        required=True)
    parser.add_argument('-n', '--num-osds',
                        help='Number of OSD units to deploy',
                        default=3)
    parser.add_argument('-c', '--channel',
                        help='Channel to use for the deployed charms',
                        default='latest/edge')
    parser.add_argument('-S', '--series',
                        help='Machine series to use for the deployment',
                        default='jammy')
    parser.add_argument('-T', '--storage',
                        help='Storage specification for OSD units')
    parser.add_argument('-C', '--constraints',
                        help='Machine constraints to pass to Juju')
    parser.add_argument('-P', '--ppa',
                        help='PPA to use for Ceph packages')
    return parser.parse_args(args)


def get_list_max(lst):
    return max(int(elem) for elem in lst)


def get_machine_list(args, apps, out):
    """
    Produce a dict of machines and their constraints suitable for
    deployment, according to what the user requested.
    The 'out' parameter is a dict where the machines specific to the
    ceph-osd units are set.
    """
    num_max = max(get_list_max(x['to']) for x in apps.values() if 'to' in x)
    base = {str(i): {} for i in range(num_max)}
    constraints = {}

    if args.constraints:
        constraints = {'constraints': args.constraints}

    out['to'] = [str(i + num_max) for i in range(args.num_osds)]
    osds = {str(i + num_max): constraints for i in range(args.num_osds)}
    base.update(osds)
    return base


def make_deploy_dict(args):
    """
    Generate a dict with all the specifications for a Juju deployment.
    """
    apps = DEFAULT_APPS.copy()
    apps['woodpecker'].update({'series': args.series,
                               'charm': args.woodpecker})
    osd = {'charm': 'ch:ceph-osd', 'num_units': args.num_osds,
           'channel': args.channel}
    apps['ceph-osd'] = osd
    ret = {}


    if args.storage:
        osd['storage'] = {'osd-devices': args.storage}
    if args.ppa:
        source = {'source': args.ppa}
        for app in (osd, apps['ceph-mon'], apps['ceph-radosgw']):
            app['options'] = source

    ret['applications'] = apps
    ret['machines'] = get_machine_list(args, apps, osd)
    ret['series'] = args.series
    ret['relations'] = [
        ['vault:shared-db', 'vault-mysql-router:shared-db'],
        ['vault-mysql-router:db-router', 'mysql-innodb-cluster:db-router'],
        ['ceph-mon:osd', 'ceph-osd:mon'],
        ['woodpecker:ceph-client', 'ceph-mon:client'],
        ['ceph-radosgw:mon', 'ceph-mon:radosgw']
    ]
    return ret


def deploy(args):
    """
    Deploy a Juju model suitable to run Ceph benchmarking.
    """
    model = args.model
    if not model:
        suffix = hex(os.getpid())
        model = 'bench-%s' % suffix[2:]

    print('Deploying to model: ', model)
    add_model(model)
    data = make_deploy_dict(args)
    cur_dir = os.path.dirname(os.path.realpath(__file__))

    path = './bundle-%d' % os.getpid()
    fp = open(path, 'w')

    try:
        yaml.dump(data, fp, default_flow_style=False)
        fp.close()
        juju_deploy(path, model, test_directory=cur_dir)
    finally:
        os.remove(path)


def get_parser(name):
    def parse_fio(msg):
        pass

    def parse_rbd_bench(msg):
        pass

    def parse_rados_bench(msg):
        pass

    def dummy(msg):
        with open('/home/ubuntu/xxx', 'w') as f:
            f.write(msg)

    return dummy
    if name == 'fio':
        return parse_fio
    elif name == 'rbd-bench':
        return parse_rbd_bench
    elif name == 'rados-bench':
        return parse_rados_bench


def run_benchmark(args):
    """
    Run a specific benchmarking action and display the results.
    """
    action_name = args[0]
    parser = get_parser(action_name)
    if not parser:
        print('Invalid benchmark specified: ', action_name)
        return

    action_args = zip(*(iter(args[1:]),) * 2)
    action_args = {x[0]: x[1] for x in action_args}
    result = model.run_action(
        unit_name='woodpecker/0',
        action_name=action_name,
        action_params=action_args)

    if result.status != 'completed':
        print('Benchmark failed: ', result.data['message'])
        return

    parser(result.data['results']['message'])


def main():
    argv = sys.argv
    if len(argv) < 2:
        print('usage: ceph-bench deploy|run ...args')
        return

    command = argv[1]
    argv = argv[2:]
    if command == 'deploy':
        try:
            deploy(parse_args(argv))
        finally:
            zaza.clean_up_libjuju_thread()
            asyncio.get_event_loop().close()
    elif command == 'run':
        run_benchmark(argv)


if __name__ == '__main__':
    main()
