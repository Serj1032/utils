import argparse
import datetime
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

# TOOL_PATH = "/home/kravserg/Git/aml-utils/aml-flash-tool/tools/linux-x86/"
TOOL_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "tools/linux-x86/")

mainLoopFlag = True


def check_file(filePath):
    if not os.path.exists(filePath):
        raise RuntimeError("File {0} not exists!".format(filePath))


def exec_cmd(cmd):
    """ Executor of the shell comand

    Args:
        cmd (list): list of parameters to shell execute

    Raises:
        RuntimeError: If can't create subprocess to execute shell command

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    with subprocess.Popen(cmd, bufsize=1, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE) as proc:
        proc.wait()

        err = proc.stderr.read().decode("UTF8").strip()
        out = proc.stdout.read().decode("UTF8").strip()
        retcode = proc.returncode

        return retcode, out, err
    raise RuntimeError("Can't exec command: " + ' '.join(proc.args))


def exec_packer(args):
    """ Execute aml_image_v2_packer for manipulate with imaged

    Args:
        args (list): Arguments for aml_image_v2_packer tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    cmd = [TOOL_PATH + "aml_image_v2_packer"] + args
    return exec_cmd(cmd)


def exec_update(args):
    """ Execute update for flashing device

    Args:
        args (list): Arguments for update tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    cmd = [TOOL_PATH + "update"] + args
    return exec_cmd(cmd)


def GetChipId(devPath):
    """ Get device chip ID

    Args:
        devPath (str): path to connected device, for example 'Bus 001 Device 087: ID 1b8e:c003'

    Returns:
        [str | None]: chipid in hex representation, None - if can't get chip id
    """
    chipid = None
    log = logging.getLogger("General")
    log.info("[{0}] Read chipID".format(devPath))

    # Trying get chipid by update tool
    retcode, out, err = exec_update(["chipid", "path-" + devPath])
    if "ChipID is:" in out:
        res = re.search(r'ChipID is:(\w+)', out)
        if res:
            chipid = res.group(1)

    # If device in u-boot then 'update chipid' return ERR
    # Tryin to get chipid from uboot by update bulkcmd command
    elif "romStage not bl1/bl2" in out:
        retcode, out, err = exec_update(["bulkcmd", "path-" + devPath, "     get_chipid"])
        res = re.search(r'bulkInReply success:(\w+)', out)
        if res:
            chipid = "0x" + res.group(1)

    if chipid is not None:
        log.info("[{0}]: chipID : {1}".format(devPath, chipid))

    return chipid


class Logger:

    class MyFormatter(logging.Formatter):
        converter = datetime.datetime.fromtimestamp

        def formatTime(self, record, datefmt=None):
            ct = self.converter(record.created)
            if datefmt:
                s = ct.strftime(datefmt)
            else:
                t = ct.strftime("%Y-%m-%d %H:%M:%S")
                s = "%s,%03d" % (t, record.msecs)
            return s

    def __init__(self, logDir="logs/"):
        folder = "log-" + datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

        self.logDir = os.path.join(logDir, folder)
        if not os.path.exists(self.logDir):
            os.makedirs(self.logDir)

        self.devicesLogDir = os.path.join(self.logDir, "DevicesLog")
        if not os.path.exists(self.devicesLogDir):
            os.makedirs(self.devicesLogDir)

        self.formatter = Logger.MyFormatter(
            fmt='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S.%f')

        # Define log file for general purpose
        fileHandlerGeneral = logging.FileHandler(self.logDir + "/General.log")
        fileHandlerGeneral.setFormatter(self.formatter)

        logGeneral = logging.getLogger("General")
        logGeneral.setLevel(logging.INFO)
        logGeneral.addHandler(fileHandlerGeneral)

        # Define scenario log file
        streamHandler = logging.StreamHandler(sys.stdout)
        streamHandler.setFormatter(self.formatter)
        logGeneral.addHandler(streamHandler)

    def GetDeviceLog(self, chipID):
        """Get logging instance to save all logs of burning process

        Args:
            chipID (str): chipid of this device

        Returns:
            logging: instance of logging
        """

        logName = "{0}".format(chipID)
        logDevice = logging.getLogger(logName)

        if not logDevice.hasHandlers():
            fileHandlerDevice = logging.FileHandler(self.devicesLogDir + "/chipid_{0}.log".format(logName))
            fileHandlerDevice.setFormatter(self.formatter)

            logDevice.setLevel(logging.INFO)
            logDevice.addHandler(fileHandlerDevice)

        return logDevice


class Device:
    def __init__(self, logger, devPath, chipid):
        self.devLock = threading.Lock()

        self.chipId = chipid
        self.devPath = devPath

        self.deviceLog = logger.GetDeviceLog(chipid)
        self.generalLog = logging.getLogger("General")

        self.waitReconnect = False

    def WaitReconnect(self, timeout=20):
        self.generalLog.info("{0} Wait reconnect...".format(self.GetDesciption()))
        with self.devLock:
            self.waitReconnect = True

        while self.waitReconnect and timeout > 0:
            timeout -= 1
            time.sleep(1)

        if timeout == 0:
            raise RuntimeError("Device didn't reconnected!")

    def DetectReconnect(self, newDevPath):
        self.generalLog.info("{0} Reconnected: new devPath: {1}".format(self.GetDesciption(), newDevPath))
        with self.devLock:
            self.devPath = newDevPath
            self.waitReconnect = False

    def Identify(self, idx):
        retcode, out, err = self.RunUpdateReturn("identify", ["7"])
        match = re.search(r'firmware', out)
        if match is not None:
            match = re.search(r'(\d)-(\d)-(\d)-(\d)-(\d)-(\d)-(\d)', out)
            if match and idx < 7:
                return match.group(idx + 1)
        raise RuntimeError("Can't identify device!")

    def GetDesciption(self):
        desc = "["
        if self.devPath:
            desc += self.devPath + " "
        desc += self.chipId + "]"
        return desc

    def RunUpdateReturn(self, cmd, args=[]):
        # Kind of magic
        if any(cmd in i for i in ["bulkcmd", "tplcmd"]):
            args[0] = "     " + args[0]

        # Execute shell command
        execCmd = [cmd, "path-" + self.devPath] + args
        retcode, out, err = exec_update(execCmd)

        # Logging command in logfile
        self.deviceLog.info("Command: {0}".format(' '.join(execCmd)))
        self.deviceLog.info(10 * "-" + " Response " + 10 * "-")
        if out != "":
            self.deviceLog.info("\n" + out)
        if err != "":
            self.deviceLog.error("\n" + err)
        self.deviceLog.info(30 * "-")

        return retcode, out, err

    def RunUpdate(self, cmd, args=[]):
        retcode, out, err = self.RunUpdateReturn(cmd, args)
        match = re.match(r'ERR', out)
        if match or retcode != 0:
            return 1, out, err

        return 0, out, err

    def RunUpdateAssert(self, cmd, args=[]):
        retcode, out, err = self.RunUpdate(cmd, args)
        if retcode != 0:
            raise RuntimeError("Error execute: update {0} {1}".format(cmd, ' '.join(args)))
        return retcode, out, err


class ImageConfig:
    class Item:
        def __init__(self, file, main_type, sub_type, file_type):

            self.file = file
            self.main_type = main_type
            self.sub_type = sub_type
            self.file_type = file_type

        def ToString(self):
            return '; '.join(['{0}: {1}'.format(k, v) for k, v in self.__dict__.items()])

    def __init__(self, filePath):
        self.filePath = filePath
        self.items = {}

        regexp = re.compile(
            r'file=\"([\w.]+)\"\s+main_type=\"(\w+)\"\s+sub_type=\"(\w+)\"\s+file_type=\"(\w+)\"', re.MULTILINE)
        with open(self.filePath, 'r') as imgCfg:
            for line in imgCfg:
                res = regexp.search(line)
                if res:
                    item = self.Item(res.group(1), res.group(
                        2), res.group(3), res.group(4))
                    self.items[item.sub_type] = item

    def GetPartitions(self):
        partitions = []
        for item in self.items.values():
            if item.main_type == "PARTITION":
                partitions.append(item)
        return partitions

    def GetFileBySubType(self, sub_type):
        return self.items.get(sub_type, None)

    def ToString(self):
        return '\n'.join(['{0}: [{1}]'.format(k, v.ToString()) for k, v in self.items.items()])


class PlatformConfig:
    def __init__(self, filePath):

        with open(filePath, 'r') as file:
            text = ''.join(file.readlines())
            self.Platform = self.ParseVariable(r'Platform:(\w+)', text)
            self.BinPara = self.ParseVariable(r'BinPara:(\w+)', text)
            self.DDRLoad = self.ParseVariable(r'DDRLoad:(\w+)', text)
            self.DDRRun = self.ParseVariable(r'DDRRun:(\w+)', text)
            self.DDRSize = self.ParseVariable(r'DDRSize:(\w+)', text)
            self.Uboot_down = self.ParseVariable(r'Uboot_down:(\w+)', text)
            self.Uboot_decomp = self.ParseVariable(r'Uboot_decomp:(\w+)', text)
            self.Uboot_enc_down = self.ParseVariable(r'Uboot_enc_down:(\w+)', text)
            self.Uboot_enc_run = self.ParseVariable(r'Uboot_enc_run:(\w+)', text)
            self.UbootLoad = self.ParseVariable(r'UbootLoad:(\w+)', text)
            self.UbootRun = self.ParseVariable(r'UbootRun:(\w+)', text)
            self.bl2ParaAddr = self.ParseVariable(r'bl2ParaAddr:(\w+)', text)

    def ParseVariable(self, regexp, line, default=""):
        res = re.search(regexp, line)
        if res:
            return res.group(1)
        return default

    def ToString(self):
        return '\n'.join(['{0}: {1}'.format(k, v) for k, v in self.__dict__.items()])


class Image:

    def __init__(self, imgPath, ubootFile=None):
        self.generalLog = logging.getLogger("General")

        self.ubootFile = ubootFile

        self.generalLog.info("Unpacking image '{0}' ...".format(imgPath))

        # Create temp dir for unpack image
        self.tmpdir = "/tmp/aml_image_unpack_xxx"
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)
        os.mkdir(self.tmpdir)

        # Unpacking image
        self.generalLog.info("Extract image '{0}' to '{1}'".format(imgPath, self.tmpdir))
        retcode, out, err = exec_packer(["-d", imgPath, self.tmpdir])
        if "Image unpack OK!" not in out:
            self.generalLog.error("Unpack result:\n{0}".format(out))
            raise RuntimeError("Can't unpackage image!")

        self.generalLog.info("Unpack result:\n{0}".format(out))
        self.generalLog.info("Image {0} successfully unpackaged".format(imgPath))

        # Read image config file
        imageConfigPath = os.path.join(self.tmpdir, "image.cfg")
        check_file(imageConfigPath)

        self.imageConfig = ImageConfig(imageConfigPath)
        self.generalLog.info("Image config:\n{0}".format(
            self.imageConfig.ToString()))

        # Read platform config file
        platformConfigFile = self.imageConfig.GetFileBySubType('platform')
        if platformConfigFile:
            platformFilePath = os.path.join(self.tmpdir, platformConfigFile.file)

            check_file(platformFilePath)

            self.platformConfig = PlatformConfig(platformFilePath)
            self.generalLog.info("Platform config:\n{0}".format(self.platformConfig.ToString()))
        else:
            raise RuntimeError("Can't find platform config in image.cfg!")

    def Cleanup(self):
        if os.path.exists(self.tmpdir):
            self.generalLog.info("Cleanup tmp directory: " + self.tmpdir)
            shutil.rmtree(self.tmpdir)

    def GetDDR(self, soc):
        ddr = None
        if any(soc == item for item in ["gxl", "axg", "txlx"]):
            ddr = os.path.join(TOOL_PATH, "usbbl2runpara_ddrinit.bin")
            check_file(ddr)
        return ddr

    def GetFIP(self, soc):
        fip = None
        if any(soc == item for item in ["gxl", "axg", "txlx"]):
            fip = os.path.join(TOOL_PATH, "usbbl2runpara_runfipimg.bin")
            check_file(fip)
        elif soc == "m8":
            fip = os.path.join(TOOL_PATH, "decompressPara_4M.dump")
            check_file(fip)
        return fip

    def GetBootloader(self):
        if self.imageConfig.items.get("bootloader") is not None:
            bootloader_file = os.path.join(self.tmpdir, self.imageConfig.items["bootloader"].file)
            check_file(bootloader_file)
            return bootloader_file
        else:
            self.generalLog.error("Can't find bootloader file!")
            exit(1)

    def GetDTB(self, soc):
        if any(soc == item for item in ["gxl", "axg", "txlx", "g12a"]):
            if self.imageConfig.items["_aml_dtb"]:
                dtbfile = os.path.join(self.tmpdir, self.imageConfig.items["_aml_dtb"].file)
        elif soc == "m8":
            if self.imageConfig.items["meson"]:
                dtbfile = os.path.join(self.tmpdir, self.imageConfig.items["meson"].file)
        else:
            raise RuntimeError("Unknown soc: " + soc)

        check_file(dtbfile)
        return dtbfile

    def GetBL2(self, soc, secure):
        bl2 = None
        if self.ubootFile is None:
            if secure is False:
                bl2 = os.path.join(self.tmpdir, self.imageConfig.items["DDR"].file)
            else:
                if self.imageConfig.items.get("DDR_ENC") is None:
                    raise RuntimeError(
                        "Your board is secured but the image you want to flash does not contain any signed bootloader!"
                    )
                else:
                    bl2 = os.path.join(self.tmpdir, self.imageConfig.items["DDR_ENC"].file)
        else:
            check_file(self.ubootFile)

            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                bl2 = os.path.join(self.tmpdir, "uboot_file_bl2.bin")
                if not os.path.exists(bl2):
                    exec_cmd(["dd", "&>/dev/null", "if=" + self.ubootFile, "of=" + bl2, "bs=49152", "count=1"])
            else:
                bl2 = os.path.join(self.tmpdir, self.imageConfig.items["DDR"].file)

        if bl2 is not None:
            check_file(bl2)

        return bl2

    def GetTPL(self, soc, secure):
        tpl = None
        if self.ubootFile is None:
            if secure is False:
                if self.imageConfig.items.get("UBOOT_COMP") is not None:
                    tpl = os.path.join(self.tmpdir, self.imageConfig.items["UBOOT_COMP"].file)
                else:
                    tpl = os.path.join(self.tmpdir, self.imageConfig.items["UBOOT"].file)
            else:
                if self.imageConfig.items.get("UBOOT_ENC") is None:
                    raise RuntimeError(
                        "Your board is secured but the image you want to flash does not contain any signed bootloader!"
                    )
                else:
                    tpl = os.path.join(self.tmpdir, self.imageConfig.items["UBOOT_ENC"].file)
        else:
            check_file(self.ubootFile)

            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                tpl = os.path.join(self.tmpdir, "uboot_file_tpl.bin")
                if not os.path.exists(tpl):
                    exec_cmd(["dd", "&>/dev/null", "if=" + self.ubootFile, "of=" + tpl, "bs=49152", "skip=1"])
            else:
                tpl = self.ubootFile

        if tpl is not None:
            check_file(tpl)

        return tpl


class Burner(threading.Thread):

    def __init__(self, img, device, args):
        threading.Thread.__init__(self)
        self.img = img
        self.device = device

        self.generalLog = logging.getLogger("General")

        # Constant arguments
        self.ubootFile = None   # TODO not implemented
        self.efuseFile = args.efuse_file
        self.reset = args.reset
        self.soc = args.soc
        self.parts = args.parts
        self.destroy = args.destroy
        self.wipe = args.wipe

        # Device denepds
        self.secure = None

        self.daemon = True
        self.start()

    def GeneralLog(self, msg, level=logging.INFO):
        self.generalLog.log(level, "%s %s", self.device.GetDesciption(), msg)
        self.DeviceLog(msg, level)

    def DeviceLog(self, msg, level=logging.INFO):
        self.device.deviceLog.log(level, "%s", msg)

    def run(self):
        self.GeneralLog("Start burning")

        try:
            self.DestroyBoot()

            # self.CheckUSBLockedByPassword() # TODO not implemented
            # self.UnloclUSBByPassword() # TODO not implemented

            self.CheckIfBoardIsSecure()

            if any(self.parts in part for part in ["all", "bootloader", "none"]):

                self.InitializingDDR()

                self.RunningUboot()

                # Need this command to avoid to loose 4 bytes of commands after reset
                self.device.RunUpdate("bulkcmd", ["echo 12345"])

                self.PrepareForLoadingPartitions()

            self.DataCachePartitionsWiping()

            self.ProgramAllPartitions()

            self.EfuseUpdate()

            self.ResettingBoard()

            self.GeneralLog("Burning done!")

        except RuntimeError as exc:
            self.GeneralLog("Burning ERROR: {0}".format(exc), logging.ERROR)

    def EfuseUpdate(self):
        if self.efuseFile is not None:
            self.GeneralLog("Programming efuses...")
            self.GeneralLog("Efuse file: " + self.efuseFile)

            check_file(self.efuseFile)

            self.GeneralLog("Debug out to don't secureboot device", logging.WARNING)
            # self.device.RunUpdateAssert("write", [self.efuseFile, "0x03000000"])
            # if self.soc == "m8":
            #     self.device.RunUpdateAssert("bulkcmd", ["efuse secure_boot_set 0x03000000"])
            # else:
            #     self.device.RunUpdateAssert("bulkcmd", ["efuse amlogic_set 0x03000000"])
            self.GeneralLog("Programming efuses - OK!")

    def ResettingBoard(self):
        if self.parts != "none":
            if self.reset:
                self.GeneralLog("Resetting board...")
                self.device.RunUpdate("bulkcmd", ["burn_complete 1"])
                self.device.WaitReconnect()

    def ProgramAllPartitions(self):
        self.GeneralLog("Programming all partitions...")

        for partition in self.img.imageConfig.GetPartitions():
            if (self.parts == "all" or self.parts == partition.sub_type or
                    (self.parts == "dtb" and partition.sub_type == "_aml_dtb")):

                if partition.sub_type == "bootloader" or (partition.sub_type == "_aml_dtb" and self.parts != "dtb"):
                    continue

                if partition.sub_type == "_aml_dtb":
                    file = self.img.GetDTB(self.soc)
                else:
                    file = partition.file

                partition_file = os.path.join(self.img.tmpdir, file)

                check_file(partition_file)

                if partition.sub_type == "_aml_dtb":
                    self.GeneralLog("Write dtb partition")
                else:
                    self.GeneralLog("Write {0} partition...".format(partition.sub_type))

                self.device.RunUpdateAssert("partition", [partition.sub_type, partition_file, partition.file_type])

                self.GeneralLog("Write {0} partition - OK!".format(partition.sub_type))

        self.GeneralLog("Programming all partitions - OK!")

    def DataCachePartitionsWiping(self):
        if self.wipe:
            self.device.RunUpdate("bulkcmd", ["setenv firstboot 1"])
            self.device.RunUpdate("bulkcmd", ["save"])
            self.device.RunUpdate("bulkcmd", ["rpmb_reset"])

        if self.soc != "m8":
            if self.wipe:
                self.GeneralLog("Wiping  data partition...")
                self.device.RunUpdate("bulkcmd", ["amlmmc erase data"])
                self.device.RunUpdate("bulkcmd", ["nand erase.part data"])
                self.GeneralLog("Wiping  data partition - OK!")

                self.GeneralLog("Wiping cache partition...")
                self.device.RunUpdate("bulkcmd", ["amlmmc erase cache"])
                self.device.RunUpdate("bulkcmd", ["nand erase.part cache"])
                self.GeneralLog("Wiping cache partition - OK!")

    def PrepareForLoadingPartitions(self):
        self.GeneralLog("Prepare for loading partitions...")

        if any(self.soc == item for item in ["gxl", "axg", "txlx", "g12a"]):
            if self.secure:
                mesonItem = self.img.imageConfig.GetFileBySubType("meson1_ENC")
            else:
                mesonItem = self.img.imageConfig.GetFileBySubType("meson1")
            if mesonItem is None:
                raise RuntimeError("Can't find meson1 file!")

            mesonFilePath = os.path.join(self.img.tmpdir, mesonItem.file)
            check_file(mesonFilePath)

            self.device.RunUpdateAssert("mwrite", [mesonFilePath, "mem", "dtb", "normal"])

            if self.parts != "none":
                self.GeneralLog("Creating partition...")
                if self.wipe:
                    self.device.RunUpdateAssert("bulkcmd", ["disk_initial 1"])
                else:
                    self.device.RunUpdateAssert("bulkcmd", ["disk_initial 0"])
                self.GeneralLog("Creating partition - OK!")

                self.GeneralLog("Writing device tree...")
                dtb = self.img.GetDTB(self.soc)
                self.GeneralLog("DTB file: " + dtb)
                self.device.RunUpdateAssert("partition", ["_aml_dtb",  dtb])
                self.GeneralLog("Writing device tree - OK!")

                self.GeneralLog("Writing bootloader...")
                bootloader = self.img.GetBootloader()
                self.GeneralLog("Bootloader file: " + bootloader)
                self.device.RunUpdateAssert("partition", ["bootloader",  bootloader])
                self.GeneralLog("Writing bootloader - OK!")
        else:
            if self.parts != "none":
                self.GeneralLog("Creating partition...")
                if self.wipe:
                    self.device.RunUpdate("bulkcmd", ["disk_initial 3"])
                    self.device.RunUpdateAssert("bulkcmd", ["disk_initial 2"])
                else:
                    self.device.RunUpdateAssert("bulkcmd", ["disk_initial 0"])
                self.GeneralLog("Creating partition - OK!")

                self.GeneralLog("Writing bootloader...")
                bootloader = self.img.GetBootloader()
                self.GeneralLog("Bootloader file: " + bootloader)
                self.device.RunUpdateAssert("partition", ["bootloader", bootloader])
                self.GeneralLog("Writing bootloader - OK!")

                self.GeneralLog("Writing device tree...")
                dtb = self.img.GetDTB(self.soc)
                self.GeneralLog("DTB file: " + dtb)
                self.device.RunUpdateAssert("mwrite", [dtb, "mem", "dtb", "normal"])
                self.GeneralLog("Writing device tree - OK!")

        if self.parts != "none":
            self.device.RunUpdate("bulkcmd", ["setenv upgrade_step 1"])
            self.device.RunUpdate("bulkcmd", ["save"])

        if "m8" == self.soc:
            self.device.RunUpdate("bulkcmd", ["save_setting"])

        self.GeneralLog("Prepare for loading partitions - OK!")

    def InitializingDDR(self):
        self.GeneralLog("Initializing DDR...")

        if any(self.soc == item for item in ["gxl", "axg", "txlx"]):

            bl2 = self.img.GetBL2(self.soc, self.secure)
            self.GeneralLog("BL2 file: " + bl2)

            ddr = self.img.GetDDR(self.soc)
            self.GeneralLog("DDR file: " + ddr)

            self.device.RunUpdateAssert("cwr", [bl2, self.img.platformConfig.DDRLoad])
            self.device.RunUpdateAssert("write", [ddr, self.img.platformConfig.bl2ParaAddr])
            self.device.RunUpdateAssert("run", [self.img.platformConfig.DDRRun])

            self.usbProtocol = self.device.Identify(4)
            if self.usbProtocol == "8":
                self.device.RunUpdateAssert("run", self.img.platformConfig.bl2ParaAddr)

        elif "g12a" == self.soc:
            tpl = self.img.GetTPL(self.soc, self.secure)
            self.GeneralLog("TPL file: " + tpl)

            self.device.RunUpdateAssert("write", [tpl, self.img.platformConfig.DDRLoad, "0x10000"])
            self.device.RunUpdateAssert("run", [self.img.platformConfig.DDRLoad])

        elif "m8" == self.soc:
            time.sleep(6)

            bl2 = self.img.GetBL2(self.soc, self.secure)
            self.GeneralLog("BL2 file: " + bl2)

            self.device.RunUpdateAssert("cwr", bl2, self.img.platformConfig.DDRLoad)
            self.device.RunUpdateAssert("run", self.img.platformConfig.DDRRun)

        time.sleep(10)
        self.GeneralLog("Initializing DDR - OK!")

    def RunningUboot(self):
        self.GeneralLog("Running u-boot...")

        if any(self.soc == item for item in ["gxl", "axg", "txlx"]):
            bl2 = self.img.GetBL2(self.soc, self.secure)
            self.GeneralLog("BL2 file: " + bl2)
            tpl = self.img.GetTPL(self.soc, self.secure)
            self.GeneralLog("TPL file: " + tpl)
            fip = self.img.GetFIP(self.soc)
            self.GeneralLog("FIP file: " + tpl)

            self.device.RunUpdateAssert("write", [bl2, self.img.platformConfig.DDRLoad])
            self.device.RunUpdateAssert("write", [fip, self.img.platformConfig.bl2ParaAddr])
            self.device.RunUpdateAssert("write", [tpl, self.img.platformConfig.UbootLoad])

            if self.usbProtocol == "8":
                self.device.RunUpdateAssert("run", [self.img.platformConfig.bl2ParaAddr])
            else:
                self.device.RunUpdateAssert("run", [self.img.platformConfig.UbootRun])

        elif "g12a" == self.soc:
            tpl = self.img.GetTPL(self.soc, self.secure)
            self.GeneralLog("TPL file: " + tpl)

            self.device.RunUpdateAssert("bl2_boot", [tpl])

        elif "m8" == self.soc:
            fip = self.img.GetFIP(self.soc)
            self.GeneralLog("FIP file: " + tpl)

            tpl = self.img.GetTPL(self.soc, self.secure)
            self.GeneralLog("TPL file: " + tpl)

            self.device.RunUpdateAssert("write", [fip, self.img.PlatformConfig.BinPara])

            if self.secure is False:
                self.device.RunUpdateAssert("write", [tpl, "0x00400000"])
                self.device.RunUpdateAssert("run", [self.img.platformConfig.Uboot_decomp])
                time.sleep(13)

                # TODO check this place
                addr = format(int(self.img.platformConfig.BinPara, 16) + 0x18, "x")
                retcode, out, err = self.device.RunUpdateReturn("rreg", ["4", "0x" + addr])
                match = re.search(addr + r":\s*(\w+)", out, re.IGNORECASE)
                if match:
                    jump_addr = "0x" + match.group(1)
                    self.device.RunUpdateAssert("run", [jump_addr])
                else:
                    raise RuntimeError("Error while running u-boot for m8")

            else:
                self.device.RunUpdateAssert("write", [tpl, self.img.platformConfig.Uboot_enc_down])
                self.device.RunUpdateAssert("run", [self.img.platformConfig.Uboot_enc_run])

        self.GeneralLog("Running u-boot - OK!")
        self.device.WaitReconnect()

    def CheckIfBoardIsSecure(self):
        self.GeneralLog("Check if board is secure...")
        if "gxl" == self.soc:
            retcode, out, err = self.device.RunUpdateReturn("rreg", ["4", "0xc8100228"])
            match = re.search(r'c8100228:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x10) == 0x10
            else:
                raise RuntimeError("Can't read secure information!")
        elif any(self.soc == item for item in ["axg", "txlx", "g12a"]):
            retcode, out, err = self.device.RunUpdateReturn("rreg", ["4", "0xff800228"])
            match = re.search(r'ff800228:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x10) == 0x10
            else:
                raise RuntimeError("Can't read secure information!")
        elif "m8" == self.soc:
            retcode, out, err = self.device.RunUpdateReturn("rreg", ["4", "0xd9018048"])
            match = re.search(r'd9018048:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x80) == 0x80
            else:
                raise RuntimeError("Can't read secure information!")

        if self.secure:
            self.GeneralLog("Board is IN secure mode")
        else:
            self.GeneralLog("Board is NOT IN secure mode")

    def DestroyBoot(self):
        self.GeneralLog("Destroy the boot...")

        if any(self.parts in part for part in ["all", "bootloader", "", "none"]):
            retcode, out, err = self.device.RunUpdate("bulkcmd", ["echo 12345"])
            if retcode == 0:
                self.GeneralLog("Rebooting the board")
                retcode, out, err = self.device.RunUpdate("bulkcmd", ["bootloader_is_old"])
                retcode, out, err = self.device.RunUpdateAssert("bulkcmd", ["erase_bootloader"])
                if self.destroy:
                    self.device.RunUpdate("bulkcmd", ["store erase boot"])
                    self.device.RunUpdate("bulkcmd", ["amlmmc erase 1"])
                    self.device.RunUpdate("bulkcmd", ["nand erase 0 4096"])

                self.device.RunUpdate("bulkcmd", ["reset"])
                self.device.WaitReconnect()
                time.sleep(8)

        self.GeneralLog("Destroy the boot - OK!")

        if self.destroy:
            exit(0)


def ParseArgs():
    def isValidFile(parser, arg):
        if os.path.exists(arg):
            return arg
        else:
            parser.error("File {0} doesn't exist!".format(arg))

    parser = argparse.ArgumentParser(
        description="Argument parsing for automated flashing Amlogic devices",
        add_help=True)
    parser.add_argument("--img", dest="img", required=True, type=lambda x: isValidFile(parser, x),
                        help="Specify location path to aml_upgrade_package.img")
    parser.add_argument("--parts", dest="parts", required=True,
                        choices=['all', 'none', 'bootloader', 'dtb', 'logo', 'recovery', 'boot', 'system'],
                        help="Specify which partition to burn")
    parser.add_argument("--wipe", dest="wipe", default=False,
                        action='store_true', help="Destroy all partitions")
    parser.add_argument("--reset", dest="reset", default=False,
                        action='store_true', help="Force reset mode at the end of the burning")
    parser.add_argument("--soc", dest="soc", choices=["gxl", "axg", "txlx", "g12a", "m8"], required=True,
                        help="Force soc type (gxl=S905/S912,axg=A113,txlx=T962,g12a=S905X2,m8=S805/A111)")
    parser.add_argument("--efuse-file", dest="efuse_file",  type=lambda x: isValidFile(parser, x), default=None,
                        help="Force efuse OTP burn, use this option carefully")
    parser.add_argument("--destroy", dest="destroy", default=False,
                        action='store_true', help="Erase the bootloader and reset the board")
    # parser.add_argument("--uboot-file") #TODO not implemented
    # parser.add_argument("--password", dest="password", type=lambda x: is_valid_file(parser, x),
    #                   help="Unlock usb mode using password file provided") #TODO not implemented

    return parser.parse_args()


def sigint_handler(sig, frame):
    global mainLoopFlag
    logging.getLogger("General").warning("Unexpected program termination: Ctrl+C")
    mainLoopFlag = False


if __name__ == "__main__":
    logger = Logger()
    generalLog = logging.getLogger("General")

    signal.signal(signal.SIGINT, sigint_handler)

    try:
        args = ParseArgs()
        img = Image(args.img)

        burners = {}
        prevDevPathes = []

        regexp_device = re.compile(r'Bus \d+ Device \d+: ID \w+:\w+', re.MULTILINE)

        while mainLoopFlag:

            retcode, out, err = exec_update(["scan"])

            for chipid in list(burners.keys()):
                if not burners[chipid].is_alive():
                    burners.pop(chipid)

            devPathes = []

            for match in regexp_device.finditer(out):
                devPath = match.group(0)
                if devPath not in prevDevPathes:
                    generalLog.info("New device: " + devPath)
                    chipid = GetChipId(devPath)
                    if chipid is not None:
                        if chipid not in burners:
                            # New device, start burning it if program not wait finish
                            burners[chipid] = Burner(img, Device(logger, devPath, chipid), args)
                        else:
                            # Burner waits reconnect of the device
                            burners[chipid].device.DetectReconnect(devPath)
                devPathes.append(devPath)
            prevDevPathes = devPathes
            time.sleep(1)

        img.Cleanup()

    except RuntimeError as exc:
        generalLog.error("Unexpected exception: {0}".format(exc))
