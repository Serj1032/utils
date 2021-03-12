#!/bin/bash

docker run -d -t -v /home/kravserg/Git/aml-utils/aml-flash-tool:/aml_flash_tool --privileged \
            -v /dev:/dev \
            -v /home/kravserg/Downloads/module2_branch-module2_userdebug_340-ROM/module2_branch-module2_userdebug_340:/aml_flash_tool/img \
            --name aml-flash-tool aml-flash