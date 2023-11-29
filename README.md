# ceph-bench
Python utility to deploy and benchmark a Ceph cluster.

# usage
This tool supports 2 operations: deployment and benchmarking. The first allows users to deploy
a Ceph cluster using Juju charms according to a set of options. The second is used to run a
benchmark action and output the results in human-readable form.

# deployment
To deploy a Ceph cluster, we can run something like the following:

    ./main.py deploy -W ./woodpecker.charm -T "10G"

This assumes we're running in the same directory as the 'main' script, and that the
Woodpecker charm has been built and packed in that same directory. Since Woodpecker doesn't
yet live on charmhub, it's necessary to build it by hand.

The tool supports other options when deploying:

* '-m' or '--model': The model to use for deployment. If not specified, a unique name will
be generated and used.

* '-W' or '--woodpecker': Path to the woodpecker charm built.

* '-n' or '--num-osds': Number of OSDs units to deploy. Defaults to 3.

* '-c' or '--channel': The channel name to use for the deployed charm. Defaults to 'latest/edge'.

* '-S' or '--series': Machine series to use for the deployment. Defaults to 'jammy'.

* '-T' or '--storage': Storage specification for OSD units. This can be used to, for example,
specify Cinder storage. If specified, it will be passed under the 'storage:osd-devices' key.

* '-C' or '--constraints': Machine constraints to pass to Juju.

* '-P' or '--ppa': PPA to use for Ceph packages. If specified, it will be passed under the
'options:source' key.

* '-R' or '--rados': Whether to deploy the RadosGW charm too. If specified, this option also
implies the deployment of additional charms (Such as Vault). Defaults to false.

# benchmarking
To run a benchmark, we can run the following:

    ./main.py run fio image-size 1024

This will run the 'fio' benchmark from the Woodpecker charm, passing the parameter 'image-size'
with the value '1024. This subcommand supports the benchmarks that Woodpecker implements.
Consult the Woodpecker documentation for further information.

Once the benchmark has run, the following will be output in human readable format:

* Elapsed time: Number of seconds the benchmark has run.
* Ops per second: Average number of IOPS per second performed.
* Bandwidth: Full bandwidth achieved, including all types of IOPS.
* Read ops: Number of read IOPS performed.
* Read ops per second: Average number of read IOPS per second performed.
* Read bandwidth: Bandwidth achieved for read IOPS.
* Write ops: Number of write IOPS performed.
* Write ops per second: Average number of write IOPS per second performed.
* Write bandwidth: Bandwidth achieved for write IOPS.
