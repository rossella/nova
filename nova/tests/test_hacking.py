#    Copyright 2014 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from nova.hacking import checks
from nova import test


class HackingTestCase(test.NoDBTestCase):
    def test_virt_driver_imports(self):
        self.assertTrue(checks.import_no_virt_driver_import_deps(
            "from nova.virt.libvirt import utils as libvirt_utils",
            "./nova/virt/xenapi/driver.py"))

        self.assertIsNone(checks.import_no_virt_driver_import_deps(
            "from nova.virt.libvirt import utils as libvirt_utils",
            "./nova/virt/libvirt/driver.py"))

        self.assertTrue(checks.import_no_virt_driver_import_deps(
            "import nova.virt.libvirt.utils as libvirt_utils",
            "./nova/virt/xenapi/driver.py"))

        self.assertTrue(checks.import_no_virt_driver_import_deps(
            "import nova.virt.libvirt.utils as libvirt_utils",
            "./nova/virt/xenapi/driver.py"))

        self.assertIsNone(checks.import_no_virt_driver_import_deps(
            "import nova.virt.firewall",
            "./nova/virt/libvirt/firewall.py"))

    def test_virt_driver_config_vars(self):
        self.assertTrue(checks.import_no_virt_driver_config_deps(
            "CONF.import_opt('volume_drivers', "
            "'nova.virt.libvirt.driver', group='libvirt')",
            "./nova/virt/xenapi/driver.py"))

        self.assertIsNone(checks.import_no_virt_driver_config_deps(
            "CONF.import_opt('volume_drivers', "
            "'nova.virt.libvirt.driver', group='libvirt')",
            "./nova/virt/libvirt/volume.py"))

    def test_virt_driver_imports(self):
        self.assertTrue(checks.no_author_tags("# author: jogo"))
        self.assertTrue(checks.no_author_tags("# @author: jogo"))
        self.assertTrue(checks.no_author_tags("# @Author: jogo"))
        self.assertTrue(checks.no_author_tags("# Author: jogo"))
        self.assertTrue(checks.no_author_tags(".. moduleauthor:: jogo"))
        self.assertFalse(checks.no_author_tags("# authorization of this"))
        self.assertEqual(2, checks.no_author_tags("# author: jogo")[0])
        self.assertEqual(2, checks.no_author_tags("# Author: jogo")[0])
        self.assertEqual(3, checks.no_author_tags(".. moduleauthor:: jogo")[0])

    def test_assert_true_instance(self):
        self.assertEqual(len(list(checks.assert_true_instance(
            "self.assertTrue(isinstance(e, "
            "exception.BuildAbortException))"))), 1)

        self.assertEqual(
            len(list(checks.assert_true_instance("self.assertTrue()"))), 0)

    def test_assert_equal_type(self):
        self.assertEqual(len(list(checks.assert_equal_type(
            "self.assertEqual(type(als['QuicAssist']), list)"))), 1)

        self.assertEqual(
            len(list(checks.assert_equal_type("self.assertTrue()"))), 0)

    def test_assert_equal_none(self):
        self.assertEqual(len(list(checks.assert_equal_none(
            "self.assertEqual(A, None)"))), 1)

        self.assertEqual(len(list(checks.assert_equal_none(
            "self.assertEqual(None, A)"))), 1)

        self.assertEqual(
            len(list(checks.assert_equal_none("self.assertIsNone()"))), 0)

    def test_no_translate_debug_logs(self):
        self.assertEqual(len(list(checks.no_translate_debug_logs(
            "LOG.debug(_('foo'))", "nova/scheduler/foo.py"))), 1)

        self.assertEqual(len(list(checks.no_translate_debug_logs(
            "LOG.debug('foo')", "nova/scheduler/foo.py"))), 0)

        self.assertEqual(len(list(checks.no_translate_debug_logs(
            "LOG.info(_('foo'))", "nova/scheduler/foo.py"))), 0)
