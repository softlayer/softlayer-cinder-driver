.. SoftLayer Cinder Driver

SoftLayer Cinder Driver
=======================

The OpenStack Block Storage Service, code named Cinder, allows users to choose one or more back-ends to create block storages. Since each back-end has different way to create/access the storage, back-ends are managed using drivers, for each storage technology or service there can be a separate driver. Adminitrator choose drivers depending on the infrastructure she has. More information about driver API can be found `here <https://github.com/openstack/cinder/blob/master/doc/source/devref/drivers.rst>`_. The SoftLayer Cinder Driver is one such driver, it allows user to use SoftLayer's block storage service from within OpenStack. This means user can create local storages as well as storages on SoftLayer.

Installation and Configuration
==============================

Installation
------------

Copy the *SoftLayerOpenStack-x.x.x.tar.gz* on the *cinder volume node*. Then run following command to install it:

.. code-block:: bash

    $ sudo pip install <location of SoftLayerOpenStack-x.x.x.tar.gz>

The SoftLayer iSCSI targets requires *CHAP* authentication to discover, this requires the driver to change */etc/iscsi/iscsid.conf* file using *sed*. For this reason, before configuring Cinder to use SoftLayer Cinder Driver, you'll need to run following command.

.. code-block:: bash

    $ # NOTE: before you run the command
    $ # check '/etc/cinder/rootwrap.d/volume.filters' exists, if not check cinder.conf for rootwrap location
    $ echo "sed:CommandFilter,/bin/sed,root" | sudo tee -a /etc/cinder/rootwrap.d/volume.filters

The SoftLayer Cinder Driver can now be configured to be used by cinder volume component.


Configuration
-------------

To make *cinder volume* component use SoftLayer Cinder Driver you need to change the *volume_driver* value in */etc/cinder/cinder.conf*

.. code-block:: python

    volume_driver=slos.cinder.driver.iscsi.SoftLayerISCSIDriver
    # if iSCSI pool is to be used use following driver
    volume_driver=slos.cinder.driver.iscsi.SoftLayerISCSIPoolDriver

The next thing to do is provide SoftLayer API access credentials in *DEFAULT* section:

.. code-block:: python

    sl_username=<YOUR_SL_USERNAME>
    sl_api_key=<YOUR_SL_KEY>

Optionally you need to specify the datacenter in which you want the volumes to be created, if not specified default value will be *dal05*

.. code-block:: python

    sl_datacenter=<datanceter_name> # default: dal05

