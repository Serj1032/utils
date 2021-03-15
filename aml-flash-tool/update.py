import sys
import os
import time
import usb.core
import usb.util
import threading


def BulkCmd(dev, Command):
    REQ_BULKCMD = 0x34
    print(b'Sending command: ' + Command)
    Command += b'\0'
    dev.ctrl_transfer(0x40, REQ_BULKCMD, 0, 2, Command)
    return ep_in.read(ep_in.wMaxPacketSize)


def Identify(dev):
    ret = dev.ctrl_transfer(0xC0, 0x20, 0x00, 0x00, 0x07)
    print("Identify:" + '-'.join('{0}'.format(x) for x in ret))


def GetChipId(dev):
    ret = dev.ctrl_transfer(0xC0, 0x40, 0x00, 0x01, 0x40)
    idArr = ret[20:32]
    chipId = "ChipId: 0x" + ''.join('{:02x}'.format(x) for x in idArr)
    print(chipId)
    return chipId

# def BulkCmd(dev, cmd):


def Cwr(dev, file):
    ret = dev.ctrl_transfer(0x40, 0x01, 0x00, 0x01, 0x40)


def BL2Parser(dev, ep_in, ep_out):
    ret = dev.ctrl_transfer(0x40, 0x50, 0x200, 0x00)
    buff = ep_in.read(0x200)
    print(buff)


def WriteOrCwr(dev, file):



if __name__ == "__main__":
    print("Start update tool...")

    dev = None
    while dev is None:
        dev = usb.core.find(idVendor=0x1b8e, idProduct=0xc003)
        if dev is None:
            time.sleep(1)

    print("Found device!")

    dev.default_timeout = 10 * 1000
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_in = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    ep_out = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

    Identify(dev)
    GetChipId(dev)
    # BL2Parser(dev, ep_in, ep_out)


    # ret = dev.ctrl_transfer(0x40, 0x30, 0x00, 0x01, "get_chipid")
    # print(ret)

    # ret = dev.ctrl_transfer(0xC0, 0x30, 0x00, 0x00, 0x08)
    # print(ret)


