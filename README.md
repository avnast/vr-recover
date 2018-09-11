# vr-recover.py
A script for recovering virtual machine backed up by vSphere Replication 
without vSphere Replication Appliance and even vCenter. May be useful if 
you somehow lost vCenter and/or Replication Appliance, but still have 
replicated data.

**NO WARRANTIES!!!** Tested only with vSphere 6.5u1 + vSphere Replication 8.1
 
Limitations:
* **replication must be performed without quiescing**
* VM disk configuration and NVRAM must not be changed between recovery points
 (is it possible anyway?)
 
Usage:

`python vr-recover.py <folder with replicated VM data>`

Can be uploaded to datastore and run directly on ESXi host with built-in 
python interpreter (ESXi 6.5 has python 3 as /bin/python). Will try to do
next things:
1) read replica config from hbrgrp.*.txt
2) create snapshot (.vmsn file) for each vSphere Replication point in time
3) create .vmsn file
4) create .vmx and .nvram files
5) make `backup` folder inside source VM folder and move replication config
 files to it, so operation can be reversed
 
 After script run you have folder with virtual machine with snapshot for each
 recovery point. You can register VM by pointing it's .vmx file in vSphere 
 datastore browser. Script doesn't touch any virtual machine disk data, so if 
 something went wrong, you can restore initial folder state:
 
 `cd <VM folder>`
 
 `rm *.vmx *.nvram *.vmsn *.vmsd`
 
 `mv backup/* .`
 
 `rmdir backup`
 
**NOTE:** be careful if you copy .vmdk files for thin disks! best to avoid it because 
thin disks are sparse files, and copying not within vSphere datastore browser
will damage them