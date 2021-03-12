FROM python:3.6-slim-buster

ARG USER_ID=1000
ARG GROUP_ID=1000
ARG USERNAME=user

RUN groupadd -g $GROUP_ID $USERNAME && \
    useradd --create-home --shell /bin/bash --uid $USER_ID --gid $GROUP_ID $USERNAME

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y \
        libusb-0.1-4 \
        usbutils

RUN mkdir -p /etc/udev/rules.d
RUN echo "SUBSYSTEM==\"usb\",  ENV{.LOCAL_ifNum}=\"$attr{devnum}\", \
         ENV{DEVTYPE}==\"usb_device\", ATTR{idVendor}==\"1b8e\", \
         ATTR{idProduct}==\"c003\", MODE:=\"0666\"" > /etc/udev/rules.d/70-persistent-usb-ubuntu14.rules

WORKDIR /aml_flash_tool
USER $USERNAME

CMD python flash_tool.py --img=/aml_flash_tool/img/aml_upgrade_package.img --parts=all --wipe --reset --soc=g12a
# ENTRYPOINT [ "python3.6" ]

