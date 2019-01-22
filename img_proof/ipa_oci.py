# -*- coding: utf-8 -*-

"""Cloud module for testing Oracle OCI images."""

# Copyright (c) 2019 SUSE LLC. All rights reserved.
#
# This file is part of ipa. Ipa provides an api and command line
# utilities for testing images in the Public Cloud.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import base64
import oci

from img_proof import ipa_utils
from img_proof.ipa_constants import (
    OCI_DEFAULT_TYPE,
    OCI_DEFAULT_USER
)
from img_proof.ipa_exceptions import OCICloudException
from img_proof.ipa_cloud import IpaCloud


class OCICloud(IpaCloud):
    """Cloud framework class for testing Oracle OCI images."""

    def __init__(
        self,
        cleanup=None,
        config=None,
        description=None,
        distro_name=None,
        early_exit=None,
        history_log=None,
        image_id=None,
        inject=None,
        instance_type=None,
        log_level=30,
        no_default_test_dirs=False,
        cloud_config=None,
        region=None,
        results_dir=None,
        running_instance_id=None,
        ssh_private_key_file=None,
        ssh_user=None,
        subnet_id=None,
        test_dirs=None,
        test_files=None,
        timeout=None,
        collect_vm_info=None,
        compartment_id=None,
        availability_domain=None
    ):
        """Initialize OCI cloud framework class."""
        super(OCICloud, self).__init__(
            'oci',
            cleanup,
            config,
            description,
            distro_name,
            early_exit,
            history_log,
            image_id,
            inject,
            instance_type,
            log_level,
            no_default_test_dirs,
            cloud_config,
            region,
            results_dir,
            running_instance_id,
            test_dirs,
            test_files,
            timeout,
            collect_vm_info,
            ssh_private_key_file,
            ssh_user,
            subnet_id
        )

        self.availability_domain = (
            availability_domain or self.ipa_config['availability_domain']
        )

        if not self.availability_domain:
            raise OCICloudException(
                'Availability domain is required to connect to OCI.'
            )

        self.ssh_user = self.ssh_user or OCI_DEFAULT_USER
        self.compartment_id = (
            compartment_id or self.ipa_config['compartment_id']
        )
        self.subnet_id = subnet_id or self.ipa_config['subnet_id']

        if not self.ssh_private_key_file:
            raise OCICloudException(
                'SSH private key file is required to connect to instance.'
            )

        config = self._get_config(self.cloud_config)
        self.compute_client = oci.core.ComputeClient(config)
        self.vnet_client = oci.core.VirtualNetworkClient(config)

    def _create_internet_gateway(self, vcn, display_name):
        internet_gateway = self.vnet_client.create_internet_gateway(
            oci.core.models.CreateInternetGatewayDetails(
                display_name=display_name + '-gateway',
                compartment_id=vcn.compartment_id,
                is_enabled=True,
                vcn_id=vcn.id
            )
        ).data

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_internet_gateway(internet_gateway.id),
            'lifecycle_state',
            'AVAILABLE'
        )

        self._add_route_rule_to_gateway(internet_gateway, vcn)

        return internet_gateway

    def _delete_internet_gateway(self, gateway_id):
        self.vnet_client.delete_internet_gateway(gateway_id)

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_internet_gateway(gateway_id),
            'lifecycle_state',
            'TERMINATED',
            succeed_on_not_found=True
        )

    def _get_gateway_in_vcn_by_name(self, vcn_id, display_name):
        gateways = self._list_gateways_in_vcn(self.compartment_id, vcn_id)

        gateway = None
        for ig in gateways:
            if ig.display_name == display_name:
                gateway = ig
                break

        return gateway

    def _list_gateways_in_vcn(self, compartment_id, vcn_id):
        gateways = oci.pagination.list_call_get_all_results(
            self.vnet_client.list_internet_gateways,
            compartment_id=compartment_id,
            vcn_id=vcn_id
        ).data

        return gateways

    def _create_vcn(self, display_name, cidr_block='10.0.0.0/29'):
        vcn = self.vnet_client.create_vcn(
            oci.core.models.CreateVcnDetails(
                cidr_block=cidr_block,
                display_name=display_name + '-vnet',
                compartment_id=self.compartment_id
            )
        ).data

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_vcn(vcn.id),
            'lifecycle_state',
            'AVAILABLE'
        )

        return vcn

    def _delete_vcn(self, vcn_id):
        self.vnet_client.delete_vcn(vcn_id)

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_vcn(vcn_id),
            'lifecycle_state',
            'TERMINATED',
            succeed_on_not_found=True
        )

    def _create_subnet(self, availability_domain, vcn, display_name):
        subnet = self.vnet_client.create_subnet(
            oci.core.models.CreateSubnetDetails(
                compartment_id=vcn.compartment_id,
                availability_domain=availability_domain,
                display_name=display_name + '-subnet',
                vcn_id=vcn.id,
                cidr_block=vcn.cidr_block
            )
        ).data

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_subnet(subnet.id),
            'lifecycle_state',
            'AVAILABLE'
        )

        return subnet

    def _get_subnet(self, subnet_id):
        return self.vnet_client.get_subnet(subnet_id)

    def _delete_subnet(self, subnet_id):
        self.vnet_client.delete_subnet(subnet_id)

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_subnet(subnet_id),
            'lifecycle_state',
            'TERMINATED',
            succeed_on_not_found=True
        )

    def _add_route_rule_to_gateway(self, internet_gateway, vcn):
        result = self.vnet_client.get_route_table(
            vcn.default_route_table_id
        ).data
        route_rules = result.route_rules

        route_rules.append(
            oci.core.models.RouteRule(
                cidr_block='0.0.0.0/0',
                network_entity_id=internet_gateway.id
            )
        )

        self.vnet_client.update_route_table(
            vcn.default_route_table_id,
            oci.core.models.UpdateRouteTableDetails(route_rules=route_rules)
        )

        result = oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_route_table(vcn.default_route_table_id),
            'lifecycle_state',
            'AVAILABLE'
        ).data

        return result

    def _clear_route_rules(self, vcn):
        self.vnet_client.update_route_table(
            vcn.default_route_table_id,
            oci.core.models.UpdateRouteTableDetails(route_rules=[])
        )

        oci.wait_until(
            self.vnet_client,
            self.vnet_client.get_route_table(vcn.default_route_table_id),
            'lifecycle_state',
            'AVAILABLE'
        )

    @staticmethod
    def _get_config(config_file=None, profile_name=None):
        kwargs = {}

        if config_file:
            kwargs['file_location'] = config_file

        if profile_name:
            kwargs['profile_name'] = profile_name

        return oci.config.from_file(kwargs)

    def _get_instance(self):
        """Retrieve instance matching instance_id."""
        try:
            instance = self.compute_client.get_instance(
                self.running_instance_id
            ).data
        except Exception:
            raise OCICloudException(
                'Instance with ID: {instance_id} not found.'.format(
                    instance_id=self.running_instance_id
                )
            )
        return instance

    def _get_instance_state(self):
        """
        Attempt to retrieve the state of the instance.

        Raises:
            OCICloudException: If the instance cannot be found.
        """
        instance = self._get_instance()

        try:
            state = instance.lifecycle_state
        except Exception:
            raise OCICloudException(
                'Instance with id: {instance_id}, '
                'cannot be found.'.format(
                    instance_id=self.running_instance_id
                )
            )

        return state

    def _get_vnic_attachments(self, compartment_id, instance_id):
        vnic_attachments = oci.pagination.list_call_get_all_results(
            self.compute_client.list_vnic_attachments,
            compartment_id=compartment_id,
            instance_id=instance_id
        ).data

        return vnic_attachments

    def _is_instance_running(self):
        """
        Return True if instance is in running state.
        """
        return self._get_instance_state().lower() == 'running'

    def _launch_instance(self):
        """Launch an instance of the given image."""
        display_name = ipa_utils.generate_instance_name('oci-ipa-test')

        vcn = self._create_vcn(display_name)
        subnet = self._create_subnet(
            vcn, self.availability_domain, display_name
        )
        self._create_internet_gateway(vcn, display_name)

        instance_metadata = {
            'user_data': base64.b64encode(self._get_user_data()).decode()
        }

        launch_instance_details = oci.core.models.LaunchInstanceDetails(
            display_name=display_name,
            compartment_id=self.compartment_id,
            availability_domain=self.availability_domain,
            shape=self.instance_type or OCI_DEFAULT_TYPE,
            metadata=instance_metadata,
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=self.image_id
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet.id
            )
        )

        launch_instance_response = self.compute_client.launch_instance(
            launch_instance_details
        ).data

        self.running_instance_id = launch_instance_response.id
        self._wait_on_instance('RUNNING', self.timeout)

    def _set_image_id(self):
        """If existing image used get image id."""
        instance = self._get_instance()
        self.image_id = instance.source_details.image_id

    def _set_instance_ip(self):
        """
        Retrieve instance ip and cache it.
        """
        instance = self._get_instance()
        self.instance_ip = None

        vnic_attachments = self._get_vnic_attachments(
            instance.compartment_id,
            instance.id
        )

        for attachment in vnic_attachments:
            vnic = self.vnet_client.get_vnic(
                attachment.vnic_id
            ).data

            public_address = vnic.public_ip
            private_address = vnic.private_ip

            if public_address or private_address:
                # Current nic has an IP address, set and finish
                self.instance_ip = public_address or private_address
                break

        if not self.instance_ip:
            raise OCICloudException(
                'IP address for instance cannot be found.'
            )

    def _start_instance(self):
        """Start the instance."""
        instance = self._get_instance()
        instance.instance_action('START')
        self._wait_on_instance('RUNNING', self.timeout)

    def _stop_instance(self):
        """Stop the instance."""
        instance = self._get_instance()
        instance.instance_action('STOP')
        self._wait_on_instance('STOPPED', self.timeout)

    def _terminate_instance(self):
        """Terminate the instance."""
        instance = self._get_instance()
        name = instance.display_name

        self.compute_client.terminate_instance(
            self.running_instance_id
        )

        oci.wait_until(
            self.compute_client,
            instance,
            'lifecycle_state',
            'TERMINATED',
            succeed_on_not_found=True
        )

        vnic_attachments = self._get_vnic_attachments(
            instance.compartment_id,
            instance.id
        )

        vcn_id = None
        for attachment in vnic_attachments:
            subnet_id = attachment.subnet_id
            subnet = self._get_subnet(subnet_id)

            if subnet.display_name == name + '-subnet':
                vcn_id = subnet.vcn_id
                gateway = self._get_gateway_in_vcn_by_name(
                    vcn_id,
                    name + '-gateway'
                )
                break

        if vcn_id:
            self._clear_route_rules(vcn_id)

            if gateway:
                self._delete_internet_gateway(gateway.id)

            self._delete_subnet(subnet_id)
            self._delete_vcn(vcn_id)
