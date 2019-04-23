# Copyright 2017 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json

from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes
from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import log
from charmhelpers.contrib.network.ip import format_ipv6_addr

from charmhelpers.contrib.storage.linux.ceph import (
    CephBrokerRq,
    is_request_complete,
    send_request_if_needed,
)


class CephClientRequires(RelationBase):
    scope = scopes.GLOBAL

    auto_accessors = ['auth', 'key']

    @hook('{requires:ceph-client}-relation-{joined}')
    def joined(self):
        self.set_state('{relation_name}.connected')

    @hook('{requires:ceph-client}-relation-{changed,departed}')
    def changed(self):
        data = {
            'key': self.key(),
            'auth': self.auth(),
            'mon_hosts': self.mon_hosts()
        }
        if all(data.values()):
            self.set_state('{relation_name}.available')

        all_requests = self.get_local(key='broker_reqs')
        if all_requests:
            incomplete = []
            for name, json_rq in all_requests.items():
                req = json.loads(json_rq)
                if not is_request_complete(req['ops']):
                    incomplete.append(name)
            if len(incomplete) == 0:
                self.set_state('{relation_name}.pools.available')
            else:
                log("incomplete requests {}.".format(incomplete.join(', ')))

    @hook('{requires:ceph-client}-relation-{broken}')
    def broken(self):
        self.remove_state('{relation_name}.available')
        self.remove_state('{relation_name}.connected')
        self.remove_state('{relation_name}.pools.available')

    def create_pool(self, name, replicas=3):
        """
        Request pool setup

        @param name: name of pool to create
        @param replicas: number of replicas for supporting pools
        """
        # json.dumps of the CephBrokerRq()
        requests = self.get_local(key='broker_reqs') or {}

        if name not in requests:
            rq = CephBrokerRq()
            rq.add_op_create_pool(name="{}".format(name),
                                  replica_count=replicas,
                                  weight=None)
            if not requests:
                requests = {}

            requests[name] = rq.request
            self.set_local(key='broker_reqs', value=requests)
            send_request_if_needed(rq, relation=self.relation_name)
            self.remove_state('{relation_name}.pools.available')
        else:
            rq = CephBrokerRq()
            try:
                j = json.loads(requests[name])
                rq.ops = j['ops']
                send_request_if_needed(rq, relation=self.relation_name)
            except ValueError as err:
                log("Unable to decode broker_req: {}.  Error: {}".format(
                    requests[name], err))

    def create_pools(self, names, replicas=3):
        """
        Request pools setup

        @param name: list of pool names to create
        @param replicas: number of replicas for supporting pools
        """
        # json.dumps of the CephBrokerRq()
        requests = self.get_local(key='broker_reqs') or {}

        new_names = [name for name in names if name not in requests]

        # existing names get ignored here
        # new names get added to a single request
        if new_names:
            rq = CephBrokerRq()
            for name in new_names:
                rq.add_op_create_pool(name="{}".format(name),
                                      replica_count=replicas,
                                      weight=None)
                requests[name] = rq.request
            self.set_local(key='broker_reqs', value=requests)
            send_request_if_needed(rq, relation=self.relation_name)
            self.remove_state('{relation_name}.pools.available')

    def get_remote_all(self, key, default=None):
        """Return a list of all values presented by remote units for key"""
        # TODO: might be a nicer way todo this - written a while back!
        values = []
        for conversation in self.conversations():
            for relation_id in conversation.relation_ids:
                for unit in hookenv.related_units(relation_id):
                    value = hookenv.relation_get(key,
                                                 unit,
                                                 relation_id) or default
                    if value:
                        values.append(value)
        return list(set(values))

    def mon_hosts(self):
        """List of all monitor host public addresses"""
        hosts = []
        addrs = self.get_remote_all('ceph-public-address')
        for addr in addrs:
            hosts.append('{}:6789'.format(format_ipv6_addr(addr) or addr))
        hosts.sort()
        return hosts
