# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
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
"""
Tests For Scheduler
"""

import mock

import mox
from oslo.config import cfg
from oslo import messaging

from nova.compute import api as compute_api
from nova.compute import task_states
from nova.compute import utils as compute_utils
from nova.compute import vm_states
from nova.conductor.tasks import live_migrate
from nova import context
from nova import db
from nova import exception
from nova.image import glance
from nova.objects import instance as instance_obj
from nova import rpc
from nova.scheduler import driver
from nova.scheduler import manager
from nova import servicegroup
from nova import test
from nova.tests import fake_instance
from nova.tests import fake_server_actions
from nova.tests.image import fake as fake_image
from nova.tests import matchers
from nova.tests.objects import test_instance_fault
from nova.tests.scheduler import fakes
from nova import utils

CONF = cfg.CONF


class SchedulerManagerTestCase(test.NoDBTestCase):
    """Test case for scheduler manager."""

    manager_cls = manager.SchedulerManager
    driver_cls = driver.Scheduler
    driver_cls_name = 'nova.scheduler.driver.Scheduler'

    def setUp(self):
        super(SchedulerManagerTestCase, self).setUp()
        self.flags(scheduler_driver=self.driver_cls_name)
        self.stubs.Set(compute_api, 'API', fakes.FakeComputeAPI)
        self.manager = self.manager_cls()
        self.context = context.RequestContext('fake_user', 'fake_project')
        self.topic = 'fake_topic'
        self.fake_args = (1, 2, 3)
        self.fake_kwargs = {'cat': 'meow', 'dog': 'woof'}
        fake_server_actions.stub_out_action_events(self.stubs)

    def test_1_correct_init(self):
        # Correct scheduler driver
        manager = self.manager
        self.assertIsInstance(manager.driver, self.driver_cls)

    def test_show_host_resources(self):
        host = 'fake_host'

        compute_node = {'host': host,
                        'compute_node': [{'vcpus': 4,
                                          'vcpus_used': 2,
                                          'memory_mb': 1024,
                                          'memory_mb_used': 512,
                                          'local_gb': 1024,
                                          'local_gb_used': 512}]}
        instances = [{'project_id': 'project1',
                      'vcpus': 1,
                      'memory_mb': 128,
                      'root_gb': 128,
                      'ephemeral_gb': 0},
                     {'project_id': 'project1',
                      'vcpus': 2,
                      'memory_mb': 256,
                      'root_gb': 384,
                      'ephemeral_gb': 0},
                     {'project_id': 'project2',
                      'vcpus': 2,
                      'memory_mb': 256,
                      'root_gb': 256,
                      'ephemeral_gb': 0}]

        self.mox.StubOutWithMock(db, 'service_get_by_compute_host')
        self.mox.StubOutWithMock(db, 'instance_get_all_by_host')

        db.service_get_by_compute_host(self.context, host).AndReturn(
                compute_node)
        db.instance_get_all_by_host(self.context, host).AndReturn(instances)

        self.mox.ReplayAll()
        result = self.manager.show_host_resources(self.context, host)
        expected = {'usage': {'project1': {'memory_mb': 384,
                                           'vcpus': 3,
                                           'root_gb': 512,
                                           'ephemeral_gb': 0},
                              'project2': {'memory_mb': 256,
                                           'vcpus': 2,
                                           'root_gb': 256,
                                           'ephemeral_gb': 0}},
                    'resource': {'vcpus': 4,
                                 'vcpus_used': 2,
                                 'local_gb': 1024,
                                 'local_gb_used': 512,
                                 'memory_mb': 1024,
                                 'memory_mb_used': 512}}
        self.assertThat(result, matchers.DictMatches(expected))

    def _mox_schedule_method_helper(self, method_name):
        # Make sure the method exists that we're going to test call
        def stub_method(*args, **kwargs):
            pass

        setattr(self.manager.driver, method_name, stub_method)

        self.mox.StubOutWithMock(self.manager.driver,
                method_name)

    def test_run_instance_exception_puts_instance_in_error_state(self):
        fake_instance_uuid = 'fake-instance-id'
        inst = {"vm_state": "", "task_state": ""}

        self._mox_schedule_method_helper('schedule_run_instance')
        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        request_spec = {'instance_properties': inst,
                        'instance_uuids': [fake_instance_uuid]}

        self.manager.driver.schedule_run_instance(self.context,
                request_spec, None, None, None, None, {}, False).AndRaise(
                        exception.NoValidHost(reason=""))
        old, new_ref = db.instance_update_and_get_original(self.context,
                fake_instance_uuid,
                {"vm_state": vm_states.ERROR,
                 "task_state": None}).AndReturn((inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context,
                new_ref, mox.IsA(exception.NoValidHost), mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager.run_instance(self.context, request_spec,
                None, None, None, None, {}, False)

    def test_live_migration_schedule_novalidhost(self):
        inst = {"uuid": "fake-instance-id",
                "vm_state": vm_states.ACTIVE,
                "task_state": task_states.MIGRATING, }

        dest = None
        block_migration = False
        disk_over_commit = False

        self.mox.StubOutWithMock(self.manager, '_schedule_live_migration')
        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        self.manager._schedule_live_migration(self.context,
                    inst, dest, block_migration, disk_over_commit).AndRaise(
                    exception.NoValidHost(reason=""))
        db.instance_update_and_get_original(self.context, inst["uuid"],
                                {"vm_state": inst['vm_state'],
                                 "task_state": None,
                                 "expected_task_state": task_states.MIGRATING,
                                }).AndReturn((inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, inst,
                                mox.IsA(exception.NoValidHost),
                                mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager = utils.ExceptionHelper(self.manager)
        self.assertRaises(exception.NoValidHost,
                          self.manager.live_migration,
                          self.context, inst, dest, block_migration,
                          disk_over_commit)

    def test_live_migration_compute_service_notavailable(self):
        inst = {"uuid": "fake-instance-id",
                "vm_state": vm_states.ACTIVE,
                "task_state": task_states.MIGRATING, }

        dest = 'fake_host'
        block_migration = False
        disk_over_commit = False

        self.mox.StubOutWithMock(self.manager, '_schedule_live_migration')
        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        self.manager._schedule_live_migration(self.context,
                    inst, dest, block_migration, disk_over_commit).AndRaise(
                    exception.ComputeServiceUnavailable(host="src"))
        db.instance_update_and_get_original(self.context, inst["uuid"],
                                {"vm_state": inst['vm_state'],
                                 "task_state": None,
                                 "expected_task_state": task_states.MIGRATING,
                                }).AndReturn((inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, inst,
                                mox.IsA(exception.ComputeServiceUnavailable),
                                mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager = utils.ExceptionHelper(self.manager)
        self.assertRaises(exception.ComputeServiceUnavailable,
                          self.manager.live_migration,
                          self.context, inst, dest, block_migration,
                          disk_over_commit)

    def test_live_migrate(self):
        instance = {'host': 'h'}
        self.mox.StubOutClassWithMocks(live_migrate, "LiveMigrationTask")
        task = live_migrate.LiveMigrationTask(self.context, instance,
                    "dest", "bm", "doc")
        task.execute()

        self.mox.ReplayAll()
        self.manager.live_migration(self.context, instance, "dest",
                                    "bm", "doc")

    def test_live_migration_set_vmstate_error(self):
        inst = {"uuid": "fake-instance-id",
                "vm_state": vm_states.ACTIVE, }

        dest = 'fake_host'
        block_migration = False
        disk_over_commit = False

        self.mox.StubOutWithMock(self.manager, '_schedule_live_migration')
        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        self.manager._schedule_live_migration(self.context,
                    inst, dest, block_migration, disk_over_commit).AndRaise(
                    ValueError)
        db.instance_update_and_get_original(self.context, inst["uuid"],
                                {"vm_state": vm_states.ERROR,
                                }).AndReturn((inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, inst,
                                mox.IsA(ValueError),
                                mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager = utils.ExceptionHelper(self.manager)
        self.assertRaises(ValueError,
                          self.manager.live_migration,
                          self.context, inst, dest, block_migration,
                          disk_over_commit)

    def test_prep_resize_no_valid_host_back_in_active_state(self):
        fake_instance_uuid = 'fake-instance-id'
        fake_instance = {'uuid': fake_instance_uuid}
        inst = {"vm_state": "", "task_state": ""}

        self._mox_schedule_method_helper('select_destinations')

        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        request_spec = {'instance_type': 'fake_type',
                        'instance_uuids': [fake_instance_uuid],
                        'instance_properties': {'uuid': fake_instance_uuid}}
        kwargs = {
                'context': self.context,
                'image': 'fake_image',
                'request_spec': request_spec,
                'filter_properties': 'fake_props',
                'instance': fake_instance,
                'instance_type': 'fake_type',
                'reservations': list('fake_res'),
        }
        self.manager.driver.select_destinations(
            self.context, request_spec, 'fake_props').AndRaise(
                exception.NoValidHost(reason=""))
        old_ref, new_ref = db.instance_update_and_get_original(self.context,
                fake_instance_uuid,
                {"vm_state": vm_states.ACTIVE, "task_state": None}).AndReturn(
                        (inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, new_ref,
                mox.IsA(exception.NoValidHost), mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager.prep_resize(**kwargs)

    def test_prep_resize_no_valid_host_back_in_shutoff_state(self):
        fake_instance_uuid = 'fake-instance-id'
        fake_instance = {'uuid': fake_instance_uuid, "vm_state": "stopped"}
        inst = {"vm_state": "stopped", "task_state": ""}

        self._mox_schedule_method_helper('select_destinations')

        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        request_spec = {'instance_type': 'fake_type',
                        'instance_uuids': [fake_instance_uuid],
                        'instance_properties': {'uuid': fake_instance_uuid}}
        kwargs = {
                'context': self.context,
                'image': 'fake_image',
                'request_spec': request_spec,
                'filter_properties': 'fake_props',
                'instance': fake_instance,
                'instance_type': 'fake_type',
                'reservations': list('fake_res'),
        }
        self.manager.driver.select_destinations(
            self.context, request_spec, 'fake_props').AndRaise(
                exception.NoValidHost(reason=""))
        old_ref, new_ref = db.instance_update_and_get_original(self.context,
                fake_instance_uuid,
                {"vm_state": vm_states.STOPPED, "task_state": None}).AndReturn(
                        (inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, new_ref,
                mox.IsA(exception.NoValidHost), mox.IgnoreArg())

        self.mox.ReplayAll()
        self.manager.prep_resize(**kwargs)

    def test_prep_resize_exception_host_in_error_state_and_raise(self):
        fake_instance_uuid = 'fake-instance-id'
        fake_instance = {'uuid': fake_instance_uuid}

        self._mox_schedule_method_helper('select_destinations')

        self.mox.StubOutWithMock(compute_utils, 'add_instance_fault_from_exc')
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')

        request_spec = {
            'instance_properties': {'uuid': fake_instance_uuid},
            'instance_uuids': [fake_instance_uuid]
        }
        kwargs = {
                'context': self.context,
                'image': 'fake_image',
                'request_spec': request_spec,
                'filter_properties': 'fake_props',
                'instance': fake_instance,
                'instance_type': 'fake_type',
                'reservations': list('fake_res'),
        }

        self.manager.driver.select_destinations(
            self.context, request_spec, 'fake_props').AndRaise(
                test.TestingException('something happened'))

        inst = {
            "vm_state": "",
            "task_state": "",
        }
        old_ref, new_ref = db.instance_update_and_get_original(self.context,
                fake_instance_uuid,
                {"vm_state": vm_states.ERROR,
                 "task_state": None}).AndReturn((inst, inst))
        compute_utils.add_instance_fault_from_exc(self.context, new_ref,
                mox.IsA(test.TestingException), mox.IgnoreArg())

        self.mox.ReplayAll()

        self.assertRaises(test.TestingException, self.manager.prep_resize,
                          **kwargs)

    def test_set_vm_state_and_notify_adds_instance_fault(self):
        request = {'instance_properties': {'uuid': 'fake-uuid'}}
        updates = {'vm_state': 'foo'}
        fake_inst = {'uuid': 'fake-uuid'}

        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')
        self.mox.StubOutWithMock(db, 'instance_fault_create')
        self.mox.StubOutWithMock(rpc, 'get_notifier')
        notifier = self.mox.CreateMockAnything()
        rpc.get_notifier('scheduler').AndReturn(notifier)
        db.instance_update_and_get_original(self.context, 'fake-uuid',
                                            updates).AndReturn((None,
                                                                fake_inst))
        db.instance_fault_create(self.context, mox.IgnoreArg()).AndReturn(
            test_instance_fault.fake_faults['fake-uuid'][0])
        notifier.error(self.context, 'scheduler.foo', mox.IgnoreArg())
        self.mox.ReplayAll()

        self.manager._set_vm_state_and_notify('foo', {'vm_state': 'foo'},
                                              self.context, None, request)

    def test_select_hosts_throws_rpc_clientexception(self):
        self.mox.StubOutWithMock(self.manager.driver, 'select_destinations')

        self.manager.driver.select_destinations(self.context, {}, {}).AndRaise(
                exception.NoValidHost(reason=""))

        self.mox.ReplayAll()
        self.assertRaises(messaging.ExpectedException,
                          self.manager.select_hosts,
                          self.context, {}, {})

    def test_prep_resize_post_populates_retry(self):
        self.manager.driver = fakes.FakeFilterScheduler()

        image = 'image'
        instance_uuid = 'fake-instance-id'
        instance = fake_instance.fake_db_instance(uuid=instance_uuid)

        instance_properties = {'project_id': 'fake', 'os_type': 'Linux'}
        instance_type = "m1.tiny"
        request_spec = {'instance_properties': instance_properties,
                        'instance_type': instance_type,
                        'instance_uuids': [instance_uuid]}
        retry = {'hosts': [], 'num_attempts': 1}
        filter_properties = {'retry': retry}
        reservations = None

        hosts = [dict(host='host', nodename='node', limits={})]

        self._mox_schedule_method_helper('select_destinations')
        self.manager.driver.select_destinations(
            self.context, request_spec, filter_properties).AndReturn(hosts)

        self.mox.StubOutWithMock(self.manager.compute_rpcapi, 'prep_resize')
        self.manager.compute_rpcapi.prep_resize(self.context, image,
                mox.IsA(instance_obj.Instance),
                instance_type, 'host', reservations, request_spec=request_spec,
                filter_properties=filter_properties, node='node')

        self.mox.ReplayAll()
        self.manager.prep_resize(self.context, image, request_spec,
                filter_properties, instance, instance_type, reservations)

        self.assertEqual([['host', 'node']],
                         filter_properties['retry']['hosts'])


class SchedulerTestCase(test.NoDBTestCase):
    """Test case for base scheduler driver class."""

    # So we can subclass this test and re-use tests if we need.
    driver_cls = driver.Scheduler

    def setUp(self):
        super(SchedulerTestCase, self).setUp()
        self.stubs.Set(compute_api, 'API', fakes.FakeComputeAPI)

        def fake_show(meh, context, id):
            if id:
                return {'id': id, 'min_disk': None, 'min_ram': None,
                        'name': 'fake_name',
                        'status': 'active',
                        'properties': {'kernel_id': 'fake_kernel_id',
                                       'ramdisk_id': 'fake_ramdisk_id',
                                       'something_else': 'meow'}}
            else:
                raise exception.ImageNotFound(image_id=id)

        fake_image.stub_out_image_service(self.stubs)
        self.stubs.Set(fake_image._FakeImageService, 'show', fake_show)
        self.image_service = glance.get_default_image_service()

        self.driver = self.driver_cls()
        self.context = context.RequestContext('fake_user', 'fake_project')
        self.topic = 'fake_topic'
        self.servicegroup_api = servicegroup.API()

    def test_hosts_up(self):
        service1 = {'host': 'host1'}
        service2 = {'host': 'host2'}
        services = [service1, service2]

        self.mox.StubOutWithMock(db, 'service_get_all_by_topic')
        self.mox.StubOutWithMock(servicegroup.API, 'service_is_up')

        db.service_get_all_by_topic(self.context,
                self.topic).AndReturn(services)
        self.servicegroup_api.service_is_up(service1).AndReturn(False)
        self.servicegroup_api.service_is_up(service2).AndReturn(True)

        self.mox.ReplayAll()
        result = self.driver.hosts_up(self.context, self.topic)
        self.assertEqual(result, ['host2'])

    def test_handle_schedule_error_adds_instance_fault(self):
        instance = {'uuid': 'fake-uuid'}
        self.mox.StubOutWithMock(db, 'instance_update_and_get_original')
        self.mox.StubOutWithMock(db, 'instance_fault_create')
        db.instance_update_and_get_original(self.context, instance['uuid'],
                                            mox.IgnoreArg()).AndReturn(
                                                (None, instance))
        db.instance_fault_create(self.context, mox.IgnoreArg()).AndReturn(
            test_instance_fault.fake_faults['fake-uuid'][0])
        self.mox.StubOutWithMock(rpc, 'get_notifier')
        notifier = self.mox.CreateMockAnything()
        rpc.get_notifier('scheduler').AndReturn(notifier)
        notifier.error(self.context, 'scheduler.run_instance', mox.IgnoreArg())
        self.mox.ReplayAll()

        driver.handle_schedule_error(self.context,
                                     exception.NoValidHost('test'),
                                     instance['uuid'], {})


class SchedulerDriverBaseTestCase(SchedulerTestCase):
    """Test cases for base scheduler driver class methods
       that will fail if the driver is changed.
    """

    def test_unimplemented_schedule_run_instance(self):
        fake_request_spec = {'instance_properties':
                {'uuid': 'uuid'}}

        self.assertRaises(NotImplementedError,
                         self.driver.schedule_run_instance,
                         self.context, fake_request_spec, None, None, None,
                         None, None, False)

    def test_unimplemented_select_destinations(self):
        self.assertRaises(NotImplementedError,
                self.driver.select_destinations, self.context, {}, {})


class SchedulerInstanceGroupData(test.TestCase):

    driver_cls = driver.Scheduler

    def setUp(self):
        super(SchedulerInstanceGroupData, self).setUp()
        self.user_id = 'fake_user'
        self.project_id = 'fake_project'
        self.context = context.RequestContext(self.user_id, self.project_id)
        self.driver = self.driver_cls()

    def _get_default_values(self):
        return {'name': 'fake_name',
                'user_id': self.user_id,
                'project_id': self.project_id}

    def _create_instance_group(self, context, values, policies=None,
                               metadata=None, members=None):
        return db.instance_group_create(context, values, policies=policies,
                                        metadata=metadata, members=members)


class SchedulerV3PassthroughTestCase(test.TestCase):
    def setUp(self):
        super(SchedulerV3PassthroughTestCase, self).setUp()
        self.manager = manager.SchedulerManager()
        self.proxy = manager._SchedulerManagerV3Proxy(self.manager)

    def test_select_destination(self):
        with mock.patch.object(self.manager, 'select_destinations'
                ) as select_destinations:
            self.proxy.select_destinations(None, None, None)
            select_destinations.assert_called_once_with(None,
                                                        filter_properties=None,
                                                        request_spec=None)

    def test_run_instance(self):
        with mock.patch.object(self.manager, 'run_instance'
                ) as run_instance:
            self.proxy.run_instance(None, None, None, None, None, None, None,
                    None)
            run_instance.assert_called_once_with(None, legacy_bdm_in_spec=None,
                                                 request_spec=None,
                                                 is_first_time=None,
                                                 filter_properties=None,
                                                 admin_password=None,
                                                 injected_files=None,
                                                 requested_networks=None)

    def test_prep_resize(self):
        with mock.patch.object(self.manager, 'prep_resize'
                ) as prep_resize:
            self.proxy.prep_resize(None, None, None, None, None, None, None)
            prep_resize.assert_called_once_with(None, request_spec=None,
                                                instance_type=None,
                                                reservations=None,
                                                image=None,
                                                filter_properties=None,
                                                instance=None)
