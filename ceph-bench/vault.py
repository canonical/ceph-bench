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

import base64
import functools
import logging
import tempfile

import zaza.model
import zaza.openstack.utilities.cert
import zaza.openstack.utilities.generic
import zaza.openstack.utilities.exceptions as zaza_exceptions


class VaultFacade:
    """Provide a facade for interacting with vault.

    For example to setup new vault deployment::

        vault_svc = VaultFacade()
        vault_svc.unseal()
        vault_svc.authorize()
    """

    def __init__(self, cacert=None, initialize=True):
        """Create a facade for interacting with vault.

        :param cacert: Path to CA cert used for vaults api cert.
        :type cacert: str
        :param initialize: Whether to initialize vault.
        :type initialize: bool
        """
        self.clients = get_clients(cacert=cacert)
        self.vip_client = get_vip_client(cacert=cacert)
        if self.vip_client:
            self.unseal_client = self.vip_client
        else:
            self.unseal_client = self.clients[0]
        if initialize:
            self.initialize()

    @property
    def is_initialized(self):
        """Check if vault is initialized."""
        return is_initialized(self.unseal_client)

    def initialize(self):
        """Initialise vault and store resulting credentials."""
        if self.is_initialized:
            self.vault_creds = get_credentials()
        else:
            self.vault_creds = init_vault(self.unseal_client)
            store_credentials(self.vault_creds)
            self.unseal_client = wait_and_get_initialized_client(self.clients)

    def unseal(self):
        """Unseal all the vaults clients."""
        unseal_all([self.unseal_client], self.vault_creds['keys'][0])
        wait_until_all_initialised(self.clients)
        unseal_all(self.clients, self.vault_creds['keys'][0])
        wait_for_ha_settled(self.clients)

    def authorize(self):
        """Authorize charm to perfom certain actions.

        Run vault charm action to authorize the charm to perform a limited
        set of calls against the vault API.
        """
        auth_all(self.clients, self.vault_creds['root_token'])
        wait_for_ha_settled(self.clients)
        run_charm_authorize(self.vault_creds['root_token'])


def get_cacert_file():
    """Retrieve CA cert used for vault endpoints and write to file.

    :returns: Path to file with CA cert.
    :rtype: str
    """
    cacert_file = None
    vault_config = zaza.model.get_application_config('vault')
    cacert_b64 = vault_config['ssl-ca']['value']
    if cacert_b64:
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as fp:
            fp.write(base64.b64decode(cacert_b64))
            cacert_file = fp.name
    return cacert_file


def basic_setup(cacert=None, unseal_and_authorize=False):
    """Run basic setup for vault tests.

    :param cacert: Path to CA cert used for vaults api cert.
    :type cacert: str
    :param unseal_and_authorize: Whether to unseal and authorize vault.
    :type unseal_and_authorize: bool
    """
    cacert = cacert or get_cacert_file()
    vault_svc = VaultFacade(cacert=cacert)
    if unseal_and_authorize:
        vault_svc.unseal()
        vault_svc.authorize()

def run_get_csr():
    """Retrieve CSR from vault.

    Run vault charm action to retrieve CSR from vault.

    :returns: Action object
    :rtype: juju.action.Action
    """
    return zaza.model.run_action_on_leader(
        'vault',
        'get-csr',
        action_params={})


def run_upload_signed_csr(pem, root_ca, allowed_domains):
    """Upload signed cert to vault.

    :param pem: Signed certificate text
    :type pem: str
    :param token: Root CA text.
    :type token: str
    :param allowed_domains: List of domains that may have certs issued from
                            certificate.
    :type allowed_domains: list
    :returns: Action object
    :rtype: juju.action.Action
    """
    return zaza.model.run_action_on_leader(
        'vault',
        'upload-signed-csr',
        action_params={
            'pem': base64.b64encode(pem).decode(),
            'root-ca': base64.b64encode(root_ca).decode(),
            'allowed-domains=': allowed_domains,
            'ttl': '24h'})


def auto_initialize(cacert=None, validation_application='keystone', wait=True,
                    skip_on_absent=False):
    logging.info('Running auto_initialize')
    basic_setup(cacert=cacert, unseal_and_authorize=True)

    action = run_get_csr()
    intermediate_csr = action.data['results']['output']
    (cakey, cacertificate) = zaza.openstack.utilities.cert.generate_cert(
        'DivineAuthority',
        generate_ca=True)
    intermediate_cert = zaza.openstack.utilities.cert.sign_csr(
        intermediate_csr,
        cakey.decode(),
        cacertificate.decode(),
        generate_ca=True)
    action = run_upload_signed_csr(
        pem=intermediate_cert,
        root_ca=cacertificate,
        allowed_domains='openstack.local')
