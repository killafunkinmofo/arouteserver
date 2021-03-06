# Copyright (C) 2017 Pier Carlo Chiodi
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

import copy
import json
import os
import shutil
import tempfile
try:
    import mock
except ImportError:
    import unittest.mock as mock
import unittest
import yaml

from pierky.arouteserver.builder import TemplateContextDumper
from pierky.arouteserver.irrdb import ASSet, RSet

def load(filename):
    path = os.path.join(os.path.dirname(__file__), "irrdb_data", filename)
    with open(path, "r") as f:
        return json.load(f)

def asset_get_data(self, cmd):
    asn_list = []
    for obj_name in self.object_names:
        raw = load("asset_{}.json".format(obj_name))
        for asn in raw["asn_list"]:
            if asn not in asn_list:
                asn_list.append(asn)
    return json.dumps({"asn_list": asn_list}).encode()

def rset_get_data(self, cmd):
    prefix_list = []
    for obj_name in self.object_names:
        raw = load("rset_{}_ipv{}.json".format(obj_name, self.ip_ver))
        for prefix in raw["prefix_list"]:
            if prefix not in prefix_list:
                prefix_list.append(prefix)
    return json.dumps({"prefix_list": prefix_list}).encode()

class TestIRRDBEnricher_Base(unittest.TestCase):

    __test__ = False

    def setup_builder(self, general, clients):
        self.builder = TemplateContextDumper(
            template_dir="templates/template-context/",
            template_name="main.j2",
            cfg_general=self.write_file("general.yml", general),
            cfg_clients=self.write_file("clients.yml", clients),
            cfg_bogons="config.d/bogons.yml",
            cache_dir=self.temp_dir,
            cache_expiry=120,
            ip_ver=4
        )

    def setUp(self, *patches):
        self.temp_dir = tempfile.mkdtemp(suffix="arouteserver_unittest")

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def write_file(self, name, dic):
        path = os.path.join(self.temp_dir, name)
        with open(path, "w") as f:
            yaml.dump(dic, f, default_flow_style=False)
        return path

@mock.patch.object(ASSet, "_run_cmd", side_effect=asset_get_data, autospec=True)
@mock.patch.object(RSet, "_run_cmd", side_effect=rset_get_data, autospec=True)
class TestIRRDBEnricher_Prefixes(TestIRRDBEnricher_Base):

    __test__ = True

    GENERAL_SIMPLE = {
        "cfg": {
            "rs_as": 999,
            "router_id": "192.0.2.2",
            "filtering": {
                "irrdb": {
                    "enforce_origin_in_as_set": True,
                    "enforce_prefix_in_as_set": True
                }
            }
        }
    }
    CLIENTS_SIMPLE = {
        "clients": [
            { "asn": 1, "ip": "192.0.2.11" },
            { "asn": 2, "ip": "192.0.2.21" }
        ]
    }
    CLIENTS_EMPTY_AUTNUM = {
        "clients": [
            { "asn": 3, "ip": "192.0.2.31" }
        ]
    }

    def get_client_by_id(self, id):
        for client in self.builder.data["clients"]:
            if client["id"] == id:
                return client
        return None

    def get_client_info(self, client):
        asns = []
        prefixes = []
        for bundle_id in client["cfg"]["filtering"]["irrdb"]["as_set_bundle_ids"]:
            bundle = self.builder.data["irrdb_info"][bundle_id]
            asns.extend(bundle.asns)
            prefixes.extend(bundle.prefixes)
        return sorted(asns), ["{}/{}".format(_["prefix"], _["length"])
                              for _ in sorted(prefixes,
                                              key=lambda item: item["prefix"])]

    def test_010_autnum_only(self, *patches):
        """IRRDB enricher: autnum only"""
        self.setup_builder(self.GENERAL_SIMPLE, self.CLIENTS_SIMPLE)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1], ["1.0.0.0/8"])
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2], ["2.0.0.0/8"])
        )

    def test_010_autnum_and_asset(self, *patches):
        """IRRDB enricher: autnum + AS-SET"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE"]}}}
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO"]}}}

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12], ["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8"])
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22], ["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8"])
        )

    def test_010_autnum_asset_and_pref_white_list(self, *patches):
        """IRRDB enricher: autnum + AS-SET + prefix white list"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE"]}}}
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_pref"] = [
            {"prefix": "100.0.0.0", "length": 8}, {"prefix": "101.0.0.0", "length": 8}
        ]
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO"]}}}
        clients["clients"][1]["cfg"]["filtering"]["irrdb"]["white_list_pref"] = [
            {"prefix": "200.0.0.0", "length": 8}, {"prefix": "201.0.0.0", "length": 8}
        ]

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12], sorted(["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8",
                                      "100.0.0.0/8", "101.0.0.0/8"]))
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22], sorted(["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8",
                                      "200.0.0.0/8", "201.0.0.0/8"]))
        )

    def test_010_autnum_asset_and_ans_white_list(self, *patches):
        """IRRDB enricher: autnum + AS-SET + ASN white list"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE"]}}}
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_asn"] = [100, 101]
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO"]}}}
        clients["clients"][1]["cfg"]["filtering"]["irrdb"]["white_list_asn"] = [200, 201]

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12, 100, 101], ["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8"])
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22, 200, 201], ["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8"])
        )

    def test_010_autnum_asset_and_white_lists(self, *patches):
        """IRRDB enricher: autnum + AS-SET + prefix/ASN white list"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE"]}}}
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_asn"] = [100, 101]
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_pref"] = [
            {"prefix": "100.0.0.0", "length": 8}, {"prefix": "101.0.0.0", "length": 8}
        ]
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO"]}}}
        clients["clients"][1]["cfg"]["filtering"]["irrdb"]["white_list_asn"] = [200, 201]
        clients["clients"][1]["cfg"]["filtering"]["irrdb"]["white_list_pref"] = [
            {"prefix": "200.0.0.0", "length": 8}, {"prefix": "201.0.0.0", "length": 8}
        ]

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12, 100, 101], sorted(["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8",
                                                "100.0.0.0/8", "101.0.0.0/8"]))
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22, 200, 201], sorted(["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8",
                                                "200.0.0.0/8", "201.0.0.0/8"]))
        )

    def test_010_autnum_asset_white_lists_and_empty_asset(self, *patches):
        """IRRDB enricher: autnum + AS-SET + prefix/ASN white list + empty AS-SET"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE", "AS-EMPTY"]}}}
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_asn"] = [100, 101]
        clients["clients"][0]["cfg"]["filtering"]["irrdb"]["white_list_pref"] = [
            {"prefix": "100.0.0.0", "length": 8}, {"prefix": "101.0.0.0", "length": 8}
        ]
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO"]}}}

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12, 100, 101], sorted(["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8",
                                                "100.0.0.0/8", "101.0.0.0/8"]))
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22], ["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8"])
        )

    def test_020_empty_autnum_empty_asset(self, *patches):
        """IRRDB enricher: empty autnum + empty AS-SET"""
        self.setup_builder(self.GENERAL_SIMPLE, self.CLIENTS_EMPTY_AUTNUM)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS3_1")),
            ([], [])
        )

    def test_010_autnum_and_1_assets(self, *patches):
        """IRRDB enricher: autnum + 2 AS-SETs"""
        clients = copy.deepcopy(self.CLIENTS_SIMPLE)
        clients["clients"][0]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-ONE", "AS-TWO"]}}}
        clients["clients"][1]["cfg"] = {"filtering": {"irrdb": {"as_sets": ["AS-TWO", "AS-THREE"]}}}

        self.setup_builder(self.GENERAL_SIMPLE, clients)
        self.builder.render_template()

        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS1_1")),
            ([1, 10, 11, 12, 20, 21, 22], sorted(["1.0.0.0/8", "10.0.0.0/8", "11.0.0.0/8", "12.0.0.0/8",
                                                  "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8"]))
        )
        self.assertEqual(
            self.get_client_info(self.get_client_by_id("AS2_1")),
            ([2, 20, 21, 22, 30, 31, 32], sorted(["2.0.0.0/8", "20.0.0.0/8", "21.0.0.0/8", "22.0.0.0/8",
                                                  "30.0.0.0/8", "31.0.0.0/8", "32.0.0.0/8"]))
        )
