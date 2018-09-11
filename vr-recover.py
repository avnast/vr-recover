from argparse import ArgumentParser
import glob, re, os, sys, calendar, datetime
from struct import pack

def INFO(msg):
    sys.stdout.write(msg+"\n")

def main():
    global src_folder, hbrcfg
    # process command line
    parser = ArgumentParser(description='Recover replicated VM from HBR folder')

    parser.add_argument('replica_folder_name')
    args = parser.parse_args()

    src_folder = args.replica_folder_name

    # read replication properties from hbrgrp.GUID-*.txt
    try:
        hbrcfg_filename = glob.glob(src_folder+"/hbrgrp.*.txt")[0]
    except Exception as e:
        sys.stderr.write("Cannot find any hbrgrp.*.txt file in folder "+src_folder+"\n")
        sys.exit(-1)

    hbrcfg = read_hbrgrp_txt(hbrcfg_filename)
    INFO('### read HBR information from '+hbrcfg_filename)

    instance_count = int(hbrcfg['group']['instances'])
    INFO('restore points count: {}'.format(instance_count))
    instances = []
    for instance_index in range(instance_count):
        instance = hbrcfg['instance'][str(instance_index)]
        # add .vmx and .nvram contents to instance itself, we'll need it later
        for i, file in instance['file'].items():
            if file['fileType'] == '0':
                with open(src_folder + '/' + os.path.basename(file['path']['relpath']), 'r') as f:
                    instance['vmx'] = f.readlines()
            if file['fileType'] == '2':
                with open(src_folder + '/' + os.path.basename(file['path']['relpath']), 'rb') as f:
                    instance['nvram'] = f.read()
        # add disk(s) info
        instance['disk'] = []
        for i in range(int(instance['diskCount'])):
            disk_info = hbrcfg['disk'][str(i)]['instance'][str(instance_index)]
            instance['disk'].append({
                'node':      get_vmx_param_by_value(instance, hbrcfg['disk'][str(i)]['id'])
                                .split('.')[0],
                'filename':  os.path.basename(disk_info['path']['relpath']),
                'datastore': disk_info['path']['ds'],
                'relpath':   disk_info['path']['relpath'],
                'abspath':   '/vmfs/volumes/' + disk_info['path']['ds'] + '/' + disk_info['path']['relpath']
            })

        instances.append(instance)

    # convert instances to snapshots
    vmsd = vmsd_header(instance_count)
    # process instances in reverse order (from newest to oldest)
    for snapshot_index in range(instance_count, 0, -1):
        instance_index = snapshot_index-1
        INFO('\n### restore point '+instances[instance_index]['snapshot'])
        # create .vmsn file
        create_vmsn(instances[instance_index], snapshot_index)
        # add corresponding lines to .vmsd
        vmsd += vmsd_snapshot_description(instances[instance_index], snapshot_index)

    INFO('\n### latest state')
    # latest instance = current instance
    instance = instances[instance_count-1]
    vmname = get_vmx_value(instance, 'displayName')

    # write .vmsd file
    with open(src_folder + '/' + vmname + '.vmsd', 'w') as f:
        f.write(vmsd)
    INFO(vmname+'.vmsd successfully written')

    # latest state disk(s) info
    instance['disk'] = []
    for i in range(int(instance['diskCount'])):
        disk_info = hbrcfg['disk'][str(i)]['instance'][str(instance_count)]
        instance['disk'].append({
            'node':      get_vmx_param_by_value(instance, hbrcfg['disk'][str(i)]['id'])
                            .split('.')[0],
            'filename':  os.path.basename(disk_info['path']['relpath']),
            'datastore': disk_info['path']['ds'],
            'relpath':   disk_info['path']['relpath'],
            'abspath':   '/vmfs/volumes/' + disk_info['path']['ds'] + '/' + disk_info['path']['relpath']
        })

    # write .vmx file
    with open(src_folder + '/' + vmname + '.vmx', 'w') as f:
        f.write(get_adjusted_vmx(instance))
    INFO(vmname+'.vmx successfully written')
        
    # write .nvram file
    with open(src_folder + '/' + vmname + '.nvram', 'wb') as f:
        f.write(instance['nvram'])
    INFO(vmname+'.nvram successfully written')

    # move source files to backup folder
    backup_folder = src_folder+'/backup'  # type: basestring
    os.mkdir(backup_folder)
    INFO('\n### made "'+backup_folder+'" directory for config files backup')
    # hbrcfg.*.txt
    os.rename(hbrcfg_filename, backup_folder+'/'+os.path.basename(hbrcfg_filename))
    INFO('moved "{}" to {}'.format(hbrcfg_filename, backup_folder))
    # .vmx and .nvram
    for i, file in instance['file'].items():
        filename = os.path.basename(file['path']['relpath'])
        os.rename(src_folder+'/'+filename, backup_folder+'/'+filename)
        INFO('moved "{}" to {}'.format(filename, backup_folder))

def vmsd_header(snapshot_count):
    return '''.encoding = "UTF-8"
snapshot.lastUID = "{0}"
snapshot.current = "{0}"
snapshot.numSnapshots = "{0}"
'''.format(snapshot_count)

def vmsd_snapshot_description(instance, snapshot_index):
    global hbrcfg
    tpl = 'snapshot'+str(snapshot_index-1)+'.{} = "{}"'+"\n"
    desc = ''
    desc += tpl.format('uid', snapshot_index)
    desc += tpl.format('filename', get_snapshot_filename(instance, snapshot_index))
    if snapshot_index > 1:
        desc += tpl.format('parent', snapshot_index-1)
    desc += tpl.format('displayName', instance['snapshot'])
    desc += tpl.format('description', 'vSphere Replication instance created on '+instance['snapshot'])

    # unix time in microseconds
    if (sys.version_info > (3, 0)):
        t = int(calendar.timegm(datetime.datetime.strptime(instance['snapshot'], "%Y-%m-%d %H:%M:%S %Z").timetuple())*1000000)
    else:
        t = long(calendar.timegm(datetime.datetime.strptime(instance['snapshot'], "%Y-%m-%d %H:%M:%S %Z").timetuple()) * 1000000)
    desc += tpl.format('createTimeHigh', t>>32)
    desc += tpl.format('createTimeLow', t&0xFFFFFFFF)

    desc += tpl.format('numDisks', instance['diskCount'])
    # for each disk, get it's filename and controller
    for i in range(int(instance['diskCount'])):
        desc += tpl.format('disk'+str(i)+'.fileName', instance['disk'][i]['filename'])
        desc += tpl.format('disk'+str(i)+'.node', instance['disk'][i]['node'])

    return desc

def create_vmsn(instance, snapshot_index):
    global src_folder
    filename = get_snapshot_filename(instance, snapshot_index)
    INFO('creating '+filename)
    with open(src_folder + '/' + filename, 'wb') as f:
        #              0xBED2BED2, # vmsn magic
        #              8, # dunno
        #              1, # number of groups?
        #              'Snapshot', # group name (zero padded 64-byte)
        #              0x5C # offset of first block
        f.write(pack('<3I64sQ', 0xBED2BED2, 8, 1, b'Snapshot', 0x5C))

        vmx = get_adjusted_vmx(instance)
        vmx_size = len(vmx)
        zero_size = 8192 # 8k zeros following actual .vmx contents
        vmx_block_header = pack('<BB7sQQBB',
                                0x3F, # block flags
                                7, # len('cfgfile')
                                b'cfgFile', # block name for .vmx
                                vmx_size+zero_size, vmx_size+zero_size, # block size twice
                                0, 0 # two zero bytes at the end of block
                            )
        nvram_size = len(instance['nvram'])
        nvram_block_header = pack('<BB9sQQBB',
                                0x3F, # block flags
                                9, # len('nvramFile')
                                b'nvramFile', # block name for nvram
                                nvram_size, nvram_size, # block size twice
                                0, 0 # two zero bytes at the end of block
                            )
        total_size = \
            len(vmx_block_header)\
            +vmx_size\
            +zero_size\
            +len(nvram_block_header)\
            +nvram_size\
            +2

        f.write(pack('<Q', total_size))
        f.write(vmx_block_header)
        f.write(vmx.encode())
        f.write(pack('<{}s'.format(zero_size), b''))
        f.write(nvram_block_header)
        f.write(instance['nvram'])
        f.write(pack('<I', 0)) # two zero bytes at the end

def get_adjusted_vmx(instance):
    global hbrcfg
    #################################################
    # 1. remove all hbr_filter lines
    # 2. remove uuid.location
    # 3. disconnect network (ethernet*.startConnected = "FALSE")
    # 4. replace disks with corresponding to snapshot
    # 5. remove cdrom-image (optionally?)
    adjusted_vmx = ''
    for line in instance['vmx']:
        # remove hbr_filter lines and uuid.location
        if line.find('hbr_filter') != -1 or line.startswith('uuid.location'):
            continue

        # cdrom image
        m = re.match(r'^sata0:(\d+).fileName = ".*"$', line) # FIXME: is it always sata0?
        if not m is None:
            adjusted_vmx += 'sata0:{}.fileName = "emptyBackingString"{}'.format(m.group(1), "\n")
            continue

        # disks
        modified = False
        for disk in instance['disk']:
            if line.startswith(disk['node']+'.fileName'):
                INFO('{}.fileName = "{}"'.format(disk['node'], disk['filename']))
                adjusted_vmx += disk['node']+'.fileName = "'+disk['filename']+'"\n'
                modified = True
        if modified:
            continue

        # passthru normal lines
        adjusted_vmx += line

        # ethernet
        m = re.match(r'^ethernet(\d+).virtualDev = ".*"$', line)
        if not m is None:
            adjusted_vmx += 'ethernet{}.startConnected = "FALSE"{}'.format(m.group(1), "\n")

    return adjusted_vmx

def get_snapshot_filename(instance, snapshot_index):
    return '{}-Snapshot{}.vmsn'.format(get_vmx_value(instance, 'displayName'), snapshot_index)

def get_vmx_value(instance, param):
    for line in instance['vmx']:
        m = re.match(r'^'+param+r'\s*=\s*"(.*)"$', line)
        if not m is None:
            return m.group(1)
    return None

def get_vmx_param_by_value(instance, param_value):
    for line in instance['vmx']:
        m = re.match(r'^(\w\S*)\s*=\s*"(.*)"$', line)
        if m is not None and m.group(2) == param_value:
            return m.group(1)
    return None

def read_hbrgrp_txt(filepath):
    # helper dict class
    class BottomlessDict(dict):
        def __getitem__(self, item):
            if not item in self:
                self.setdefault(item, BottomlessDict())
            return dict.__getitem__(self, item)

    data = BottomlessDict()
    with open(filepath, 'r') as file:
        for line in file:
            m = re.match(r'^(\w\S*)\s*=\s*"(.*)"$', line)
            if m is None:
                continue

            left = m.group(1).split('.')
            right = m.group(2)
            exec('data["'+'"]["'.join(left)+'"] = right')

    return data



main()