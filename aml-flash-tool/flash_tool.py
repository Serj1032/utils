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

MainLoopFlag = True


def CheckFile(FilePath):
    """ Check existing file

    Args:
        FilePath (str): Path to file

    Raises:
        RuntimeError: File doesn't exist
    """
    if not os.path.exists(FilePath):
        raise RuntimeError("File {0} doesn't exist!".format(FilePath))


def ExecCmd(Cmd):
    """ Executor of the shell comand

    Args:
        Cmd (list): list of parameters to shell execute

    Raises:
        RuntimeError: If can't create subprocess to execute shell command

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    with subprocess.Popen(Cmd, bufsize=1, shell=False, stderr=subprocess.PIPE, stdout=subprocess.PIPE) as Proc:
        Proc.wait()

        Err = Proc.stderr.read().decode("UTF8").strip()
        Out = Proc.stdout.read().decode("UTF8").strip()
        Retcode = Proc.returncode

        return Retcode, Out, Err
    raise RuntimeError("Can't exec command: " + ' '.join(Proc.args))


def ExecPacker(Args):
    """ Execute aml_image_v2_packer for manipulate with imaged

    Args:
        Args (list): Arguments for aml_image_v2_packer tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    Cmd = [os.path.join(TOOL_PATH, "aml_image_v2_packer")] + Args
    return ExecCmd(Cmd)


def ExecUpdate(Args):
    """ Execute update for flashing device

    Args:
        Args (list): Arguments for update tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    Cmd = [TOOL_PATH + "update"] + Args
    return ExecCmd(Cmd)


def GetChipId(DevPath):
    """ Get device chip ID

    Args:
        DevPath (str): path to connected device, for example 'Bus 001 Device 087: ID 1b8e:c003'

    Returns:
        [str | None]: chipid in hex representation, None - if can't get chip id
    """
    ChipId = None
    Log = logging.getLogger("General")
    Log.info("[{0}] Reading chipID...".format(DevPath))

    # Trying get chipid by update tool
    Retcode, Out, Err = ExecUpdate(["chipid", "path-" + DevPath])
    if "ChipID is:" in Out:
        Res = re.search(r'ChipID is:(\w+)', Out)
        if Res:
            ChipId = Res.group(1)

    # If device in u-boot then 'update chipid' return ERR
    # Tryin to get chipid from uboot by 'update bulkcmd' command
    elif "romStage not bl1/bl2" in Out:
        Retcode, Out, Err = ExecUpdate(["bulkcmd", "path-" + DevPath, "     get_chipid"])
        Res = re.search(r'bulkInReply success:(\w+)', Out)
        if Res:
            ChipId = "0x" + Res.group(1)

    if ChipId is not None:
        Log.info("[{0}] chipID: {1}".format(DevPath, ChipId))

    return ChipId


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

    def __init__(self, LogDir="logs/"):
        Folder = "log-" + datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

        self.LogDir = os.path.join(LogDir, Folder)
        if not os.path.exists(self.LogDir):
            os.makedirs(self.LogDir)

        self.DevicesLogDir = os.path.join(self.LogDir, "DevicesLog")
        if not os.path.exists(self.DevicesLogDir):
            os.makedirs(self.DevicesLogDir)

        self.Formatter = Logger.MyFormatter(fmt='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S.%f')

        # Define log file for general purpose
        FileHandlerGeneral = logging.FileHandler(self.LogDir + "/General.log")
        FileHandlerGeneral.setFormatter(self.Formatter)

        LogGeneral = logging.getLogger("General")
        LogGeneral.setLevel(logging.INFO)
        LogGeneral.addHandler(FileHandlerGeneral)

        # Define scenario log file
        StreamHandler = logging.StreamHandler(sys.stdout)
        StreamHandler.setFormatter(self.Formatter)
        LogGeneral.addHandler(StreamHandler)

    def GetDeviceLog(self, ChipID):
        """Get logging instance to save all logs of burning process

        Args:
            ChipID (str): chipid of this device

        Returns:
            logging: instance of device logging
        """

        LogName = "{0}".format(ChipID)
        DeviceLog = logging.getLogger(LogName)

        if not DeviceLog.hasHandlers():
            fileHandlerDevice = logging.FileHandler(self.DevicesLogDir + "/chipid_{0}.log".format(LogName))
            fileHandlerDevice.setFormatter(self.Formatter)

            DeviceLog.setLevel(logging.INFO)
            DeviceLog.addHandler(fileHandlerDevice)

        return DeviceLog


class Device:
    def __init__(self, Logger, DevPath, ChipId):
        self.DevLock = threading.Lock()

        self.ChipId = ChipId
        self.DevPath = DevPath

        self.DeviceLog = Logger.GetDeviceLog(ChipId)
        self.GeneralLog = logging.getLogger("General")

        self.IsWaitReconnect = False

    def WaitReconnect(self, Timeout=20):
        """ Blocking waiting device reconnection

        Args:
            Timeout (int, optional): timeout to wait device reconnection. Defaults to 20.

        Raises:
            RuntimeError: If device didn't reconnect in timeout time span
        """
        self.GeneralLog.info("{0} Wait reconnect...".format(self.GetDesciption()))
        with self.DevLock:
            self.IsWaitReconnect = True

        while self.IsWaitReconnect is True and Timeout > 0:
            Timeout -= 1
            time.sleep(1)

        if Timeout == 0:
            raise RuntimeError("{0} Device didn't reconnected!".format(self.GetDesciption()))

    def DetectReconnect(self, NewDevPath):
        """ Detect device reconnect

        Args:
            NewDevPath (str): new device path after reconnect
        """
        self.GeneralLog.info("{0} Device reconnected at: {1}".format(self.GetDesciption(), NewDevPath))
        with self.DevLock:
            self.DevPath = NewDevPath
            self.IsWaitReconnect = False

    def Identify(self, Idx):
        """ Get device firmware version

        Args:
            Idx (int): get device firmware version element at position Idx

        Raises:
            RuntimeError: Can't get part of device firmware version

        Returns:
            str: Part of device firmware version
        """
        retcode, out, err = self.RunUpdateReturn("identify", ["7"])
        match = re.search(r'firmware', out)
        if match is not None:
            match = re.search(r'(\d)-(\d)-(\d)-(\d)-(\d)-(\d)-(\d)', out)
            if match is not None and Idx < 7:
                return match.group(Idx + 1)
        raise RuntimeError("Can't identify device!")

    def GetDesciption(self):
        Desc = "["
        if self.DevPath is not None:
            Desc += self.DevPath + " "
        Desc += self.ChipId + "]"
        return Desc

    def RunUpdateReturn(self, Cmd, Args=[]):
        """ Call Amlogic update tool for specify device

        Args:
            Cmd (str): name of the command
            Args (list, optional): arguments for command. Defaults to [].

        Raises:
            RuntimeError: Wrong commnd format

        Returns:
            int, str, str: Result of command execution retcode, stdout, stderr
        """
        # Kind of magic
        if any(Cmd in i for i in ["bulkcmd", "tplcmd"]):
            if len(Args) == 0:
                raise RuntimeError("Can't execute command '{0}' without args!".format(Cmd))
            else:
                Args[0] = "     " + Args[0]

        # Execute shell command
        ExecCmd = [Cmd, "path-" + self.DevPath] + Args
        Retcode, Out, Err = ExecUpdate(ExecCmd)

        # Logging command in logfile
        self.DeviceLog.info("Command: update {0}".format(' '.join(ExecCmd)))
        self.DeviceLog.info(10 * "-" + " Response " + 10 * "-")
        if Out != "":
            self.DeviceLog.info("\n" + Out)
        if Err != "":
            self.DeviceLog.error("\n" + Err)
        self.DeviceLog.info(30 * "-")

        return Retcode, Out, Err

    def RunUpdate(self, Cmd, Args=[]):
        Retcode, Out, Err = self.RunUpdateReturn(Cmd, Args)
        Match = re.match(r'ERR', Out)
        if Match is not None or Retcode != 0:
            return 1, Out, Err

        return 0, Out, Err

    def RunUpdateAssert(self, Cmd, Args=[]):
        """ Call Amlogic update tool for specify device and check for error return

        Args:
            Cmd (str): name of the command
            Args (list, optional): arguments for command. Defaults to [].

        Raises:
            RuntimeError: Command return error result

        Returns:
            int, str, str: Result of command execution retcode, stdout, stderr
        """
        Retcode, Out, Err = self.RunUpdate(Cmd, Args)
        if Retcode != 0:
            raise RuntimeError("Error execute: update {0} {1}".format(Cmd, ' '.join(Args)))
        return Retcode, Out, Err


class ImageConfig:
    class Item:
        def __init__(self, file, main_type, sub_type, file_type):

            self.file = file
            self.main_type = main_type
            self.sub_type = sub_type
            self.file_type = file_type

        def ToString(self):
            return ';\t'.join(['{0}: {1}'.format(k, v) for k, v in self.__dict__.items()])

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
        return '\n'.join(['{0}:\t[{1}]'.format(k, v.ToString()) for k, v in self.items.items()])


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
        return '\n'.join(['{0}:\t{1}'.format(k, v) for k, v in self.__dict__.items()])


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
        retcode, out, err = ExecPacker(["-d", imgPath, self.tmpdir])
        if "Image unpack OK!" not in out:
            self.generalLog.error("Unpack result:\n{0}".format(out))
            raise RuntimeError("Can't unpackage image!")

        self.generalLog.info("Unpack result:\n{0}".format(out))
        self.generalLog.info("Image {0} successfully unpackaged".format(imgPath))

        # Read image config file
        imageConfigPath = os.path.join(self.tmpdir, "image.cfg")
        CheckFile(imageConfigPath)

        self.imageConfig = ImageConfig(imageConfigPath)
        self.generalLog.info("Image config:\n{0}".format(
            self.imageConfig.ToString()))

        # Read platform config file
        platformConfigFile = self.imageConfig.GetFileBySubType('platform')
        if platformConfigFile:
            platformFilePath = os.path.join(self.tmpdir, platformConfigFile.file)

            CheckFile(platformFilePath)

            self.platformConfig = PlatformConfig(platformFilePath)
            self.generalLog.info("Platform config:\n{0}".format(self.platformConfig.ToString()))
        else:
            raise RuntimeError("Can't find platform config in image.cfg!")

    def Cleanup(self):
        if os.path.exists(self.tmpdir):
            self.generalLog.info("Cleanup tmp directory: " + self.tmpdir)
            shutil.rmtree(self.tmpdir)

    def GetDDR(self, soc):
        ddrFile = None
        if any(soc == item for item in ["gxl", "axg", "txlx"]):
            ddrFile = os.path.join(TOOL_PATH, "usbbl2runpara_ddrinit.bin")
            CheckFile(ddrFile)
        return ddrFile

    def GetFIP(self, soc):
        fip = None
        if any(soc == item for item in ["gxl", "axg", "txlx"]):
            fip = os.path.join(TOOL_PATH, "usbbl2runpara_runfipimg.bin")
            CheckFile(fip)
        elif soc == "m8":
            fip = os.path.join(TOOL_PATH, "decompressPara_4M.dump")
            CheckFile(fip)
        return fip

    def GetBootloader(self):
        if self.imageConfig.items.get("bootloader") is not None:
            bootloader_file = os.path.join(self.tmpdir, self.imageConfig.items["bootloader"].file)
            CheckFile(bootloader_file)
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

        CheckFile(dtbfile)
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
            CheckFile(self.ubootFile)

            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                bl2 = os.path.join(self.tmpdir, "uboot_file_bl2.bin")
                if not os.path.exists(bl2):
                    ExecCmd(["dd", "&>/dev/null", "if=" + self.ubootFile, "of=" + bl2, "bs=49152", "count=1"])
            else:
                bl2 = os.path.join(self.tmpdir, self.imageConfig.items["DDR"].file)

        if bl2 is not None:
            CheckFile(bl2)

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
            CheckFile(self.ubootFile)

            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                tpl = os.path.join(self.tmpdir, "uboot_file_tpl.bin")
                if not os.path.exists(tpl):
                    ExecCmd(["dd", "&>/dev/null", "if=" + self.ubootFile, "of=" + tpl, "bs=49152", "skip=1"])
            else:
                tpl = self.ubootFile

        if tpl is not None:
            CheckFile(tpl)

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
        self.device.DeviceLog.log(level, "%s", msg)

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

            CheckFile(self.efuseFile)

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

                CheckFile(partition_file)

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
            CheckFile(mesonFilePath)

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
    global MainLoopFlag
    logging.getLogger("General").warning("Unexpected program termination: Ctrl+C")
    MainLoopFlag = False


if __name__ == "__main__":
    Logger = Logger()
    GeneralLog = logging.getLogger("General")

    signal.signal(signal.SIGINT, sigint_handler)

    try:
        Args = ParseArgs()
        Img = Image(Args.img)
        GeneralLog.info("Waitnig device connect...")

        Burners = {}
        PrevDevPathes = []
        RegexpDevice = re.compile(r'Bus \d+ Device \d+: ID \w+:\w+', re.MULTILINE)

        while MainLoopFlag:

            for ChipId in list(Burners.keys()):
                if not Burners[ChipId].is_alive():
                    Burners.pop(ChipId)

            DevPathes = []
            Retcode, Out, Err = ExecUpdate(["scan"])

            for Match in RegexpDevice.finditer(Out):
                DevPath = Match.group(0)
                if DevPath not in PrevDevPathes:
                    ChipId = GetChipId(DevPath)
                    if ChipId is not None:
                        if ChipId not in Burners:
                            # New device, start burning it
                            GeneralLog.info("New device: " + DevPath)
                            Burners[ChipId] = Burner(Img, Device(Logger, DevPath, ChipId), Args)
                        else:
                            # Burner waits reconnect of the device
                            Burners[ChipId].device.DetectReconnect(DevPath)
                DevPathes.append(DevPath)
            PrevDevPathes = DevPathes
            time.sleep(1)

        Img.Cleanup()

    except RuntimeError as Exc:
        GeneralLog.error("Unexpected exception: {0}".format(Exc))
