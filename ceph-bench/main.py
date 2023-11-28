#! /usr/bin/env python3

import argparse
import ast
import asyncio
import os
import subprocess
import sys
import tempfile
import yaml

import zaza
from zaza.charm_lifecycle.deploy import deploy as juju_deploy
from zaza.controller import add_model
import zaza.model as model


BASIC_APPS = {
    'ceph-mon': {'charm': 'ch:ceph-mon', 'num_units': 3,
                 'options': {'monitor-count': 3},
                 'to': ['0', '1', '2']},
    'woodpecker': {'num_units': 1, 'to': ['3']}
}

EXTRA_APPS = {
    'ceph-radosgw': {'charm': 'ch:ceph-radosgw', 'num_units': 1,
                     'to': ['4']},
    'vault-mysql-router': {'charm': 'ch:mysql-router'},
    'mysql-innodb-cluster': {'charm': 'ch:mysql-innodb-cluster',
                             'num_units': 3, 'to': ['5', '6', '7']},
    'vault': {'charm': 'ch:vault', 'num_units': 1, 'to': ['8']},
}


def zaza_cleanup(fn):
    def inner(*args):
        try:
            fn(*args)
        finally:
          zaza.clean_up_libjuju_thread()
          asyncio.get_event_loop().close()
    return inner


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
    parser.add_argument('-R', '--rados',
                        help='Whether to deploy the Rados gateway charm',
                        action='store_true')
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

    relations = [
        ['ceph-mon:osd', 'ceph-osd:mon'],
        ['woodpecker:ceph-client', 'ceph-mon:client']
    ]

    apps = BASIC_APPS.copy()
    if args.rados:
        apps.update(EXTRA_APPS)
        relations.extend([
            ['vault:shared-db', 'vault-mysql-router:shared-db'],
            ['vault-mysql-router:db-router', 'mysql-innodb-cluster:db-router'],
            ['ceph-radosgw:mon', 'ceph-mon:radosgw']
        ])

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
        for app_name, app in apps.items():
            if 'ceph-' in app_name:
                app['options'] = source

    ret['applications'] = apps
    ret['machines'] = get_machine_list(args, apps, osd)
    ret['series'] = args.series
    ret['relations'] = relations
    return ret


@zaza_cleanup
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
        subprocess.call(['juju', 'switch', model])
        juju_deploy(path, model, test_directory=cur_dir)
    finally:
        os.remove(path)


def extract_nums(line):
    line = line.split()
    return (int(line[1]), float(line[3]), float(line[5]))


def extract_fio_info(jobs, key, out):
    ops = jobs.get(key)
    if ops:
        out[key] = (ops['total_ios'], ops['iops'], ops['bw'])


def get_parser(name):
    def parse_fio(msg):
        tab = ast.literal_eval(msg)
        jobs = tab['jobs'][0]
        ret = {'elapsed': jobs['elapsed']}
        extract_fio_info(jobs, 'read', ret)
        extract_fio_info(jobs, 'write', ret)
        return ret

    def parse_rbd_bench(msg):
        lines = msg.split('\n')
        ret = {}
        for line in lines:
            if 'read_ops' in line:
                ret['read'] = extract_nums(line)
            elif 'write_ops' in line:
                ret['write'] = extract_nums(line)
            elif 'elapsed' in line:
                ret['elapsed'] = extract_nums(line)[0]

        return ret

    def parse_rados_bench(msg):
        raise NotImplementedError('Not implemented yet')


    if name == 'fio':
        return parse_fio
    elif name == 'rbd-bench':
        return parse_rbd_bench
    elif name == 'rados-bench':
        return parse_rados_bench


def convert_action_params(action, params):
    try:
        actions = subprocess.check_output(['juju', 'actions', 'woodpecker',
                                           '--schema', '--format', 'yaml'])
        actions = yaml.safe_load(actions.decode('utf8'))
        action_keys = actions[action]['properties']
        for key, val in params.items():
            if key not in action_keys:
                continue
            typ = action_keys[key].get('type')
            if typ == 'integer':
                params[key] = int(val)
            elif typ == 'number':
                params[key] = float(val)
    except Exception as exc:
        print('Failed to update action parameters ', str(exc))


def print_results(parsed_data, name):
    print('ran benchmark: ', name)

    read_ops, write_ops = parsed_data['read'], parsed_data['write']
    num_ops = read_ops[0] + write_ops[0]
    elapsed = parsed_data['elapsed']

    print((f'elapsed time:\t{elapsed:.2f}\t'
           f'ops/sec:\t{num_ops/elapsed:.2f}\t\t'
           f'bandwidth:\t{read_ops[2]+write_ops[2]}'))
    print((f'read ops:\t{read_ops[0]}\t'
           f'read_ops/sec:\t{read_ops[1]:.2f}\t\t'
           f'read BW:\t{read_ops[2]}'))
    print((f'write ops:\t{write_ops[0]}\t'
           f'write_ops/sec:\t{write_ops[1]:.2f}\t\t'
           f'write BW:\t{write_ops[2]}'))


@zaza_cleanup
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
    convert_action_params(action_name, action_args)
    result = model.run_action(
        unit_name='woodpecker/0',
        action_name=action_name,
        action_params=action_args)

    if result.status != 'completed':
        print('Benchmark failed: ', result.data['message'])
        return

    rv = parser(result.data['results']['test-results'])
    try:
        print_results(rv, action_name)
    except Exception as exc:
        print('Failed to print results: ', str(exc))


def main():
    argv = sys.argv
    if len(argv) < 2:
        print('usage: %s deploy|run ...args' % argv[0])
        return

    command = argv[1]
    argv = argv[2:]
    if command == 'deploy':
        deploy(parse_args(argv))
    elif command == 'run':
        run_benchmark(argv)


if __name__ == '__main__':
    main()
