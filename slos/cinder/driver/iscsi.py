# vim: tabstop=4 shiftwidth=4 softtabstop=4

"""
Cinder driver for SoftLayer storage systems.


"""

from cinder import exception
from cinder import utils
from cinder.volume import driver
from cinder.volume import utils as volume_utils
from cinder.openstack.common.gettextutils import _
from cinder.openstack.common import log as logging
from cinder.openstack.common import processutils as proc_utils
from cinder.openstack.common import lockutils

from oslo.config import cfg

from SoftLayer.exceptions import SoftLayerAPIError

from . import api as api

LOG = logging.getLogger(__name__)

SL_OPTS = [
    cfg.BoolOpt('sl_pool_real_order',
                default=0,
                help='What to do when pool is fully being used. '
                     'Should new Order be placed?'),
    cfg.BoolOpt('sl_vol_order_ceil',
                default=False,
                help='Should the driver order the first smallest bigger '
                     'volume if size is not supported'),
    cfg.BoolOpt('sl_order_snap_space',
                default=True,
                help='Whether snapshot space for the volume '
                     'be automatically ordered'),
    cfg.StrOpt('sl_use_name',
               default='none',
               help="How should driver use SL volume's name. "
                    "Possible values: none, metadata, display_name."
                    " 'none'=ignore SL's display name, "
                    "'metadata'= update volume metadata with "
                    "key 'SL_iSCSI_Name',"
                    " or 'display_name' = update volume's display name with "
                    "SL's display name."),
    cfg.StrOpt('sl_datacenter',
               default='dal05',
               help='SoftLayer Datacenter'),
    cfg.StrOpt('sl_pool_volume_clear',
               default='zero',
               help='How to erase contents of the volume when deleted. '
                    'Possible values: zero, shred or none'),
    cfg.IntOpt('sl_snap_space_active_retry',
               default=10,
               help='Retry count to check snapshot space is active'),
    cfg.IntOpt('sl_snap_space_active_wait',
               default=10,
               help='Sleep wait between retry to check snap space is active'),
    cfg.IntOpt('sl_vol_active_wait',
               default=10,
               help='Sleep wait between retry to check volume is active'),
    cfg.IntOpt('sl_vol_active_retry',
               default=10,
               help='Retry count to check volume is active'),
    cfg.StrOpt('sl_username',
               default=None,
               help='SoftLayer username'),
    cfg.StrOpt('sl_api_key',
               default=None,
               help='api_key for the softlayer account',
               secret=True)]


class SoftLayerISCSIDriver(driver.ISCSIDriver):

    """SoftLayer iSCSI volume driver. Implements the driver API
    for SoftLayer iSCSI volume service. The volumes created using
    this driver, will be created in SoftLayer.
    """

    def __init__(self, *args, **kwargs):
        super(SoftLayerISCSIDriver, self).__init__(*args, **kwargs)
        self.configuration.append_config_values(SL_OPTS)
        self.vol_mgr = None
        self.meta_mgr = api.MetadataManager()
        self._stats = {}

    def do_setup(self, _):
        """Setup the SoftLayer Volume driver.

        Called one time by the manager after the driver is loaded.
        Create the softlayer client.
        """
        self.vol_mgr = api.IscsiVolumeManager(configuration=self.configuration)

    def check_for_setup_error(self):
        """Check that the driver is working and can communicate.

        Invoke a web services API to make sure we can talk to the server.
        Also perform the datacenter value verification.
        """
        try:
            LOG.debug("Checking if sed is accessible as root")
            utils.execute('sed', '-e',
                          "/discovery.sendtargets.auth.username/d", '-e',
                          "/discovery.sendtargets.auth.password/d",
                          '/etc/iscsi/iscsid.conf',
                          run_as_root=True)
        except proc_utils.ProcessExecutionError as ex:
            LOG.error(_("Uable to execute 'sed' command on "
                        "/etc/iscsi/iscsid.conf make sure you have"
                        "'sed' entry in cinder's rootwrap.conf "
                        "or check if /etc/iscsi/iscsid.conf exists"))
            raise ex
        return self.vol_mgr.check_dc()

    def create_volume(self, volume):
        """Driver entry point for creating a new volume.

        Creates a new iSCSI volume, waits for it to become available.
        Once available, update the VolumeAdminMetadata with SoftLayer
        iSCSI volume.

        :param volume: OpenStack Volume Object
        :returns model_update: a dictionary containing updates
                               to volume model. This will be used by
                               VolumeManager to update database.
        """
        sl_vol = self.vol_mgr.create_volume(volume)
        self.meta_mgr.serialize(volume['id'], sl_vol)
        return self._create_model(sl_vol, volume)

    def _create_model(self, sl_vol, volume, **kwargs):
        """
        Creates the model update which will be used by the VolumeManager
        to update the database representation of the Volume Object. Since
        the driver sometimes allows to create volumes of size greater than
        the given size, size will always be provided as an option to update.

        Other update depends on the configuration value `sl_use_name` which
        decicdes how to use the `username` (display name) of the SoftLayer
        volume.

        :param sl_vol: SoftLayer volume object
        :param volume: OpenStack Volume object.
        :param **kwargs: any other updates which are required.
        """
        model = kwargs
        model.update({'size': sl_vol['capacityGb']})
        if self.configuration.sl_use_name not in \
                ('metadata', 'none', 'display_name'):
            LOG.warn(_("Configuration variable 'sl_use_name'"
                       " must have one of the three values: "
                       "'metadata', 'none', 'display_name'."
                       " Has %s. Assuming none instead." %
                       self.configuration.sl_use_name))
            return model
        if self.configuration.sl_use_name == 'metadata':
            self.meta_mgr.update_user_meta(volume['id'],
                                           {'SL_iSCSI_Name':
                                            sl_vol['username']})
            return model
        elif self.configuration.sl_use_name == 'display_name':
            model.update({'display_name': sl_vol['username']})
        return model

    def delete_volume(self, volume):
        """Driver entry point for destroying existing volumes.

        If the iSCSI storage is created using driver then
        iSCSI storage cancel request is raised.
        """
        sl_vol = self.meta_mgr.deserialize(volume['id'])
        self.vol_mgr.cancel(sl_vol)
        self.meta_mgr.delete_all(volume['id'])

    def ensure_export(self, _, volume):
        """Driver entry point to get the export info for an existing volume."""
        sl_vol = self.meta_mgr.deserialize(volume['id'])
        return {'provider_location':
                self.vol_mgr.run_iscsiadm(sl_vol)}

    def create_export(self, context, volume):
        """Driver entry point to get the export info for a new volume."""
        return self.ensure_export(context, volume)

    def remove_export(self, context, volume):
        """Driver exntry point to remove an export for a volume.

        Since exporting is idempotent in this driver, we have nothing
        to do for unexporting.
        """
        LOG.debug(_("remove_export called"))

    def initialize_connection(self, volume, connector):
        """Driver entry point to attach a volume to an instance.

        Use the username and password of the iSCSI storage and
        using iSCSI initiator tool discrover the IQL of the iSCSI
        target so that it can be used to attach.
        """
        sl_vol = self.meta_mgr.deserialize(volume['id'])
        return self.vol_mgr.get_iscsi_properties(sl_vol)

    def terminate_connection(self, volume, connector, **kwargs):
        """Driver entry point to unattach a volume from an instance.
        """
        LOG.debug("Terminate Connection Called")

    def create_snapshot(self, snapshot):
        """Driver entry point for creating a snapshot.

        If the iSCSI storage has space for new snapshots
        then create new snapshot. Otherwise, order
        snapshot space and then create snapshot.
        """
        volume = snapshot['volume']
        sl_vol = self.meta_mgr.deserialize(volume['id'])
        sl_snap = self.vol_mgr.create_snapshot(sl_vol, snapshot)
        self.meta_mgr.update_meta(volume['id'],
                                  {snapshot['id']: sl_snap['id']})

    def delete_snapshot(self, snapshot):
        """Driver entry point for deleting a snapshot."""
        volume = snapshot['volume']
        sl_snap_id = self.meta_mgr.get(volume['id'], snapshot['id'])
        self.vol_mgr.delete_snapshot(sl_snap_id)
        self.meta_mgr.delete_entry(volume, snapshot['id'])

    def create_volume_from_snapshot(self, volume, snapshot):
        """Driver entry point for creating a new volume from a snapshot.

        Since, Snapshot is available as iSCSI storage in SoftLayer
        we copy the contents into new volume.
        """
        model_update = self.create_volume(volume)
        sl_snap_id = self.meta_mgr.get(snapshot['volume']['id'],
                                       snapshot['id'])
        sl_vol = self.meta_mgr.deserialize(volume['id'])
        try:
            self.vol_mgr.restore_snapshot(sl_snap_id, sl_vol)
        except SoftLayerAPIError as ex:
            LOG.error("Unable to restore snapshot. "
                      "Deleting newly created volume.")
            self.delete_volume(volume)
            raise exception.VolumeBackendAPIException(data=ex.message)
        return model_update

    def _attch(self, conn):
        """
        Creates the properties dict required by the brick utils
        to attache the volume.

        :param conn: connection information retrived from SL/iSCSI tools.
        """
        protocol = conn['driver_volume_type']
        LOG.debug("Attaching for protocol '%s'" % protocol)
        connector = utils.brick_get_connector(protocol)
        device = connector.connect_volume(conn['data'])
        host_device = device['path']
        if not connector.check_valid_device(host_device):
            raise exception.InvalidResults(
                "Unable to get valid device %s" %
                host_device)
        return {'conn': conn, 'device': device, 'connector': connector}

    def create_cloned_volume(self, volume, src_vref):
        """Creates clone of an existing volume.

        1. Create a new iSCSI storage.
        2. Attach it.
        3. Attach the source volume.
        4. Copy source into new volume.
        5. Detach both the volume.
        """
        self.create_volume(volume)
        new_sl_vol = self.meta_mgr.deserialize(volume['id'])
        src_sl_vol = self.meta_mgr.deserialize(src_vref['id'])
        try:
            self._copy_volume(src_sl_vol, new_sl_vol)
        except:
            self.delete_volume(volume)
            raise
        return self._create_model(new_sl_vol, volume,
                                  source_volid=src_vref['id'])

    def _copy_volume(self, src_sl_vol, new_sl_vol):
        """Creates a clone of the specified volume."""
        dest_conn = self.vol_mgr.get_iscsi_properties(new_sl_vol)
        dest_attach_info = self._attch(dest_conn)
        source_conn = self.vol_mgr.get_iscsi_properties(src_sl_vol)
        src_attach_info = self._attch(source_conn)
        LOG.debug("Both source and destination volumes "
                  "are attached successfully. Copying data.")
        size_in_mb = int(new_sl_vol['capacityGb']) * 1024
        try:
            volume_utils.copy_volume(src_attach_info['device']['path'],
                                     dest_attach_info['device']['path'],
                                     size_in_mb)
        except proc_utils.ProcessExecutionError:
            LOG.error("Error while copying data.")
            raise
        finally:
            self._detach_volume(dest_attach_info)
            self._detach_volume(src_attach_info)
        LOG.info("Successfully cloned the volume")

    def get_volume_stats(self, refresh=False):
        """Get volume status.

        Since there is no limit on the volumes created.
        Provide infinite storage available.
        """
        if not refresh:
            return self._stats
        # update the stats
        LOG.debug(_("Updating volume status"))
        data = {}
        backend_name = self.configuration.safe_get('volume_backend_name')
        data["volume_backend_name"] = backend_name or 'SoftLayer_iSCSI'
        data["vendor_name"] = 'SoftLayer'
        data["driver_version"] = '1.0'
        data["storage_protocol"] = 'iSCSI'

        data['total_capacity_gb'] = 'infinite'
        data['free_capacity_gb'] = 'infinite'
        data['reserved_percentage'] = 0
        data['QoS_support'] = False
        self._stats = data
        return self._stats


class SoftLayerISCSIPoolDriver(SoftLayerISCSIDriver):
    """
    SoftLayer Pool driver for iSCSI offerings. This driver
    tries to use existing volumes over ordering new ones.
    """

    @lockutils.synchronized('sl_create_vol', 'cinder-', False)
    def create_volume(self, volume):
        """
        Finds a free volume from pool to use,
        if not found new volume is created
        (if configuration also allows that)

        :param volume: OpenStack Volume Object.
        """
        metadata = self.meta_mgr.get_user_meta(volume['id'])
        LOG.debug(
            _("Create volume called with name: %s, size: %s, id: %s" %
              (volume['display_name'], volume['size'], volume['id'])))
        sl_vol = None
        imported = self.meta_mgr.all_imported()
        if 'softlayer_volume_id' in metadata and \
                int(metadata['softlayer_volume_id']) in imported:
            raise exception.InvalidVolume(
                reason="Volume requested is already is in use")
        elif 'softlayer_volume_id' in metadata:
            sl_vol = self.vol_mgr.use_exiting(
                volume['size'], metadata['softlayer_volume_id'])
        else:
            sl_vol = self.vol_mgr.find_free_volume(volume['size'], imported)

        if sl_vol:
            self.meta_mgr.serialize(volume['id'], sl_vol)
            return self._create_model(sl_vol, volume)
        if not self.configuration.sl_pool_real_order:
            raise exception.VolumeBackendAPIException(
                data="Storage pool has been fully utilized."
                " Configuration does not allow driver to order new storage.")
        # here we have to order a new volume.
        sl_vol = self.vol_mgr.create_volume(volume)
        self.meta_mgr.serialize(volume['id'], sl_vol)
        return self._create_model(sl_vol, volume)

    def delete_volume(self, volume):
        """
        Removes the data from volume and returns the volume to pool.

        :param volume: OpenStack Volume Object.
        """
        if self.configuration.sl_pool_volume_clear not in \
                ("zero", "shred", "none"):
            raise exception.InvalidConfigurationValue(
                option='volume_clear',
                value=self.configuration.sl_pool_volume_clear)

        sl_vol = self.meta_mgr.deserialize(volume['id'])
        connection = self.vol_mgr.get_iscsi_properties(sl_vol)
        attach_info = self._attch(connection)
        size_in_mb = 1024 * volume['size']

        try:
            if self.configuration.sl_pool_volume_clear == 'zero':
                LOG.info("zeroing out volume")
                volume_utils.copy_volume(
                    '/dev/zero', attach_info['device']['path'], size_in_mb)
            elif self.configuration.sl_pool_volume_clear == 'shred':
                LOG.info("Shredding volume")
                utils.execute('shred', '-n3', '-s%dMiB' %
                              size_in_mb, attach_info['device']['path'],
                              run_as_root=True)
        except proc_utils.ProcessExecutionError as ex:
            LOG.error(_("Error while swiping out data. %s" % ex))
            raise
        finally:
            self._detach_volume(attach_info)
        self.meta_mgr.delete_all(volume['id'])
