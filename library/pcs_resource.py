#!/usr/bin/python

DOCUMENTATION = '''
---
module: pcs_resource
short_description: wrapper module for 'pcs resource' 
description:
     - module for creating, deleting and updating clusters resources using 'pcs' utility
version_added: "0.1"
options:
  state:
    description:
      - 'present' - ensure that cluster resource exists
      - 'absent' - ensure cluster resource doesn't exist
    required: false
    default: present
    choices: [present, absent]
  name:
    description:
      - name of cluster resource
    required: true
    default: null
  resource_class:
    description:
      - class of resource
      required: true
      default: ocf
      choices: [ocf, systemd, stonith]
  resource_type:
    description:
      - cluster resource type
    required: false
    default: null
  options:
    description:
      - options for pcs
    required: false
    default: null
notes:
   - tested on CentOS 6.8, 7.3
   - no support for groups (yet)
requirements: [ ]
author: "Ondrej Famera <ondrej-xa2iel8u@famera.cz>"
'''

EXAMPLES = '''
- name: ensure Dummy resource with name 'test' is present
  pcs_resource: name='test' resource_type='Dummy'

- name: ensure that resource with name 'vip' is not present
  pcs_resource: name='vip' state='absent'

- name: ensure resource 'test2' of 'IPaddr2' type exists an has 5 second monitor interval
  pcs_resource: name='test2' resource_type='IPaddre2' options='ip=192.168.1.2 op monitor interval=5'
'''

## TODO detect if we are runnign cluster where we want to create resources

## FIXME check if we have 'pcs' command

import os.path
import xml.etree.ElementTree as ET
import tempfile

def replace_element(elem, replacement):
        elem.clear()
        elem.text = replacement.text
        elem.tail = replacement.tail
        elem.tag = replacement.tag
        elem.attrib = replacement.attrib
        elem[:] = replacement[:] 

def compare_resources(module, res1, res2):
	# we now have 2 nodes that we can compare, so lets dump them into files for comparring
        n1_file_fd, n1_tmp_path = tempfile.mkstemp()
        n2_file_fd, n2_tmp_path = tempfile.mkstemp()
        n1_file = open(n1_tmp_path, 'w')
        n2_file = open(n2_tmp_path, 'w')
        ## dump the XML resource definitions into temporary files
        sys.stdout = n1_file
	ET.dump(res1)
        sys.stdout = n2_file
	ET.dump(res2)
        sys.stdout = sys.__stdout__
        ##
        n1_file.close()
        n2_file.close()
        ## normalize the files and store results in new files - this also removes some unimportant spaces and stuff
        n3_file_fd, n3_tmp_path = tempfile.mkstemp()
        n4_file_fd, n4_tmp_path = tempfile.mkstemp()
        rc, out, err = module.run_command('xmllint --output ' + n3_tmp_path + ' ' + n1_tmp_path)
        rc, out, err = module.run_command('xmllint --output ' + n4_tmp_path + ' ' + n2_tmp_path)

        ## addd files that should be cleaned up
        module.add_cleanup_file(n1_tmp_path)
        module.add_cleanup_file(n2_tmp_path)
        module.add_cleanup_file(n3_tmp_path)
        module.add_cleanup_file(n4_tmp_path)

        ## now compare files
        diff = ''
        rc, out, err = module.run_command('diff ' + n3_tmp_path + ' ' + n4_tmp_path)
        if rc != 0:
            # if there was difference then show the diff
            n3_file = open(n3_tmp_path, 'r+')
            n4_file = open(n4_tmp_path, 'r+')
            #n3_file.seek(0)
            #n4_file.seek(0)
            diff = {
                'before_header': '',
                'before': to_native(b('').join(n3_file.readlines())),
                'after_header': '',
                'after': to_native(b('').join(n4_file.readlines())),
            }
        return rc, diff

def main():
        module = AnsibleModule(
                argument_spec = dict(
                        state=dict(default="present", choices=['present', 'absent']),
                        name=dict(required=True),
                        resource_class=dict(default="ocf", choices=['ocf', 'systemd', 'stonith']),
                        resource_type=dict(required=False),
                        options=dict(required=False),
                ),
                supports_check_mode=True
        )

        state = module.params['state']
        resource_name = module.params['name']
        resource_class = module.params['resource_class']
        if state == 'present' and (not module.params['resource_type']):
            module.fail_json(msg='When creating cluster resource you must specify the resource_type')
        result = {}

        ## get running cluster configuration
        rc, out, err = module.run_command('pcs cluster cib')
        if rc == 0:
            current_cib_root = ET.fromstring(out)
        else:
            module.fail_json(msg='Failed to load current cluster configuration')
        
        ## try to find the resource that we seek
        #resource = current_cib_root.find("./configuration/resources/primitive[@id='" + resource_name + "']") - Xpath doesn't work in python 2.6
        resource = None
        resources = current_cib_root.findall("./configuration/resources/primitive")
        for res in resources:
            if res.attrib.get('id') == resource_name:
                resource = res
                break

        if state == 'present' and resource is None:
            # resource should be present, but we don't see it in configuration - lets create it
            result['changed'] = True
            if not module.check_mode:
                if resource_class == 'stonith':
                    cmd='pcs stonith create %(name)s %(resource_type)s %(options)s' % module.params
                else:
                    cmd='pcs resource create %(name)s %(resource_type)s %(options)s' % module.params
                rc, out, err = module.run_command(cmd)
                if rc == 0:
                    module.exit_json(changed=True)
                else:
                    module.fail_json(msg="Failed to create resource: " + out)

        elif state == 'present' and resource is not None:
            # resource should be present and we have find resource with such ID - lets compare it with definition if it needs a change

	    # lets simulate how the resource would look like if it was created using command we have
	    clean_cib_fd, clean_cib_path = tempfile.mkstemp() 
	    module.add_cleanup_file(clean_cib_path)
	    module.do_cleanup_files()
            # we must be sure that clean_cib_path is empty
            if resource_class == 'stonith':
                cmd = 'pcs -f ' + clean_cib_path + ' stonith create %(name)s %(resource_type)s %(options)s' % module.params
            else:
                cmd = 'pcs -f ' + clean_cib_path + ' resource create %(name)s %(resource_type)s %(options)s' % module.params
            rc, out, err = module.run_command(cmd)
	    if rc == 0:
		## we have a comparable resource created in clean cluster, so lets select it and compare it
		clean_cib = ET.parse(clean_cib_path)	
		clean_cib_root = clean_cib.getroot()
		#clean_resource = clean_cib_root.find("./configuration/resources/primitive[@id='" + resource_name + "']") - Xpath doesn't work in python 2.6
                clean_resource = None
                clean_resources = clean_cib_root.find("./configuration/resources/primitive")
                for clean_res in clean_resources:
                    if clean_res.attrib.get('id') == resource_name:
                        clean_resource = clean_res
                        break
		if clean_resource is not None:
                    rc, diff = compare_resources(module, resource, clean_resource)
                    if rc == 0:
                        # if no differnces were find there is no need to update the resource
                        module.exit_json(changed=False)
                    else:
                        # otherwise lets replace the resource with new one
                        result['changed'] = True
                        result['diff'] = diff
                        if not module.check_mode:
                            replace_element(resource, clean_resource)
                            new_cib = ET.ElementTree(current_cib_root)
                            new_cib_fd, new_cib_path = tempfile.mkstemp()
                            module.add_cleanup_file(new_cib_path)
                            new_cib.write(new_cib_path)
                            rc, out, err = module.run_command('pcs cluster cib-push ' + new_cib_path)
                            if rc == 0:
                                module.exit_json(changed=True)
                            else:
                                module.fail_json(msg="Failed push updated configuration to cluster: " + out)

	    else:
                module.fail_json(msg="Unable to simulate resource with given definition: " + out)
		 
                   
        elif state == 'absent' and resource is not None:
            # resource should not be present but we have found something - lets remove that
            result['changed'] = True
            if not module.check_mode:
                if resource_class == 'stonith':
                    cmd='pcs stonith delete %(name)s' % module.params
                else:
                    cmd='pcs resource delete %(name)s' % module.params
                rc, out, err = module.run_command(cmd)
                if rc == 0:
                    module.exit_json(changed=True)
                else:
                    module.fail_json(msg="Failed to delete resource: " + out)

        else:
            # resource should not be present and is nto there, nothing to do
            result['changed'] = False

        ## END of module
        module.exit_json(**result)

# import module snippets
from ansible.module_utils.basic import *
main()
