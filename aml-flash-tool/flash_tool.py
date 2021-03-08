import re
import sys
import os
import subprocess
import threading
import time
import argparse
import shutil
import tempfile
import logging
import time
import datetime

TOOL_PATH = "/home/kravserg/Git/aml-utils/aml-flash-tool/tools/linux-x86/"


class ImageConfig:
    class Item:
        def __init__(self, file, main_type, sub_type, file_type):

            self.file = file
            self.main_type = main_type
            self.sub_type = sub_type
            self.file_type = file_type

        def ToString(self):
            return '; '.join(['{0}: {1}'.format(k, v) for k, v in self.__dict__.items()])

    def __init__(self, imageCfg):
        self.imageCfg = imageCfg
        self.items = {}

        regexp = re.compile(
            r'file=\"([\w.]+)\"\s+main_type=\"(\w+)\"\s+sub_type=\"(\w+)\"\s+file_type=\"(\w+)\"', re.MULTILINE)
        with open(self.imageCfg, 'r') as imgCfg:
            for line in imgCfg:
                res = regexp.search(line)
                if res:
                    item = self.Item(res.group(1), res.group(
                        2), res.group(3), res.group(4))
                    self.items[item.sub_type] = item

    def GetFileBySubType(self, sub_type):
        return self.items.get(sub_type, None)

    def ToString(self):
        return '\n'.join(['{0}: {1}'.format(k, v.ToString()) for k, v in self.items.items()])


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
            self.Uboot_enc_down = self.ParseVariable(
                r'Uboot_enc_down:(\w+)', text)
            self.Uboot_enc_run = self.ParseVariable(
                r'Uboot_enc_run:(\w+)', text)
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

    def __init__(self, imgPath, soc, parts, secure, ubootPath=None):
        self.imgPath = imgPath
        self.soc = soc
        self.secure = secure
        self.generalLog = logging.getLogger("General")
        self.uboot = None
        self.dtbfile = None
        self.ddr = None
        self.fip = None
        self.bl2 = None
        self.tpl = None
        self.imageConfig = None
        self.platformConfig = None

        self.generalLog.info("Start unpacking image {0}".format(imgPath))

        # Create temp dir for unpack image
        self.tmpdir = "/tmp/aml_image_unpack_xxx"
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)
        os.mkdir(self.tmpdir)

        # Unpacking image
        self.generalLog.info(
            "Unpack image {0} to {1}".format(self.imgPath, self.tmpdir))
        retcode, out, err = exec_packer(["-d", self.imgPath, self.tmpdir])
        self.generalLog.info("Unpack result:\n{0}".format(out))

        self.generalLog.info(
            "Image {0} successfully unpackaged".format(imgPath))

        # Read image config file
        imageConfigPath = os.path.join(self.tmpdir, "image.cfg")
        if os.path.exists(imageConfigPath):
            self.imageConfig = ImageConfig(imageConfigPath)
            self.generalLog.info("Image config:\n{0}".format(
                self.imageConfig.ToString()))
        else:
            self.generalLog.error(
                "Image config file {0} doesn't exist".format(imageConfigPath))
            exit(1)

        # Read platform config file
        platformConfigFile = self.imageConfig.GetFileBySubType('platform')
        if platformConfigFile:
            platformFilePath = os.path.join(
                self.tmpdir, platformConfigFile.file)
            if os.path.exists(platformFilePath):
                self.platformConfig = PlatformConfig(platformFilePath)
                self.generalLog.info("Platform config:\n{0}".format(
                    self.platformConfig.ToString()))
            else:
                self.generalLog.error("Can't find {0} in {1}".format(
                    platformFilePath, self.tmpdir))
                exit(1)
        else:
            self.generalLog.error("Can't find platform config in image.cfg!")
            exit(1)

        # DDR and FIP files
        if any(parts == item for item in ["all", "bootloader", "none"]):
            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                self.ddr = os.path.join(TOOL_PATH, "usbbl2runpara_ddrinit.bin")
                self.fip = os.path.join(
                    TOOL_PATH, "usbbl2runpara_runfipimg.bin")

                if not os.path.exists(self.ddr):
                    self.generalLog.error(
                        "File {0} doesn't exist!".format(self.ddr))
                    exit(1)

                if not os.path.exists(self.fip):
                    self.generalLog.error(
                        "File {0} doesn't exist!".format(self.fip))
                    exit(1)

            elif soc == "m8":
                self.fip = os.path.join(TOOL_PATH, "decompressPara_4M.dump")
                if not os.path.exists(self.fip):
                    self.generalLog.error(
                        "File {0} doesn't exist!".format(self.fip))
                    exit(1)

        # Botloader file
        if self.imageConfig.items["bootloader"]:
            self.bootloader_file = os.path.join(
                self.tmpdir, self.imageConfig.items["bootloader"].file)
            if not os.path.exists(self.bootloader_file):
                self.generalLog.error(
                    "File {0} doesn't exist!".format(self.bootloader_file))
                exit(1)
        else:
            self.generalLog.error("Can't find bootloader file!")
            exit(1)
        self.generalLog.info(
            "Botloader file: {0}".format(self.bootloader_file))

        # DTB file
        if any(soc == item for item in ["gxl", "axg", "txlx", "g12a"]):
            if self.imageConfig.items["_aml_dtb"]:
                self.dtbfile = os.path.join(
                    self.tmpdir, self.imageConfig.items["_aml_dtb"].file)
                if not os.path.exists(self.dtbfile):
                    self.generalLog.error(
                        "File {0} doesn't exist!".format(self.dtbfile))
                    exit(1)
        elif soc == "m8":
            if self.imageConfig.items["meson"]:
                self.dtbfile = os.path.join(
                    self.tmpdir, self.imageConfig.items["meson"].file)
                if not os.path.exists(self.dtbfile):
                    self.generalLog.error(
                        "File {0} doesn't exist!".format(self.dtbfile))
        self.generalLog.info("DTB file: {0}".format(self.dtbfile))

        # bl2 and tpl files
        if ubootPath == None:
            if self.secure == False:
                self.bl2 = os.path.join(
                    self.tmpdir, self.imageConfig.items["DDR"].file)
                if self.imageConfig.items.get("UBOOT_COMP"):
                    self.tpl = os.path.join(
                        self.tmpdir, self.imageConfig.items["UBOOT_COMP"].file)
                else:
                    self.tpl = os.path.join(
                        self.tmpdir, self.imageConfig.items["UBOOT"].file)
            else:
                if not self.imageConfig.items.get("DDR_ENC") or not self.imageConfig.items.get("UBOOT_ENC"):
                    self.generalLog.error(
                        "Your board is secured but the image you want to flash does not contain any signed bootloader!")
                    exit(1)
                else:
                    self.bl2 = os.path.join(
                        self.tmpdir, self.imageConfig.items["DDR_ENC"].file)
                    self.tpl = os.path.join(
                        self.tmpdir, self.imageConfig.items["UBOOT_ENC"].file)
        else:
            if not os.path.exists(ubootPath):
                self.generalLog.error(
                    "Uboot file {0} doesn't exist!".format(ubootPath))
                exit(1)
            self.generalLog.info("Uboot file: {0}".format(ubootPath))
            if any(soc == item for item in ["gxl", "axg", "txlx"]):
                self.bl2 = os.path.join(self.tmpdir, "uboot_file_bl2.bin")
                self.tpl = os.path.join(self.tmpdir, "uboot_file_tpl.bin")
                exec(["dd", "&>/dev/null", "if=" + ubootPath,
                      "of=" + self.bl2, "bs=49152", "count=1"])
                exec(["dd", "&>/dev/null", "if=" + ubootPath,
                      "of=" + self.tpl, "bs=49152", "skip=1"])
            else:
                self.bl2 = os.path.join(
                    self.tmpdir, self.imageConfig.items["DDR"].file)
                self.tpl = ubootPath
        if self.bl2 != None:
            if not os.path.exists(self.bl2):
                self.generalLog.error(
                    "File {0} doesn't exist!".format(self.bl2))
            else:
                self.generalLog.info("bl2 file: {0}".format(self.bl2))

        if self.tpl != None:
            if not os.path.exists(self.tpl):
                self.generalLog.error(
                    "File {0} doesn't exist!".format(self.tpl))
            else:
                self.generalLog.info("tpl file: {0}".format(self.tpl))


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def exec_packer(args):
    """ Execute aml_image_v2_packer for manipulate with imaged

    Args:
        args (list): Arguments for aml_image_v2_packer tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    cmd = [TOOL_PATH + "aml_image_v2_packer"] + args
    return exec(cmd)


def exec_update(args):
    """ Execute update for flashing device

    Args:
        args (list): Arguments for update tool

    Returns:
        int, str, str: Return result of execute command: retcode, stdout, stderr
    """
    cmd = [TOOL_PATH + "update"] + args
    return exec(cmd)


def exec(cmd):
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


class Burner(threading.Thread):
    def __init__(self, img,  logger, deviceName, soc, part, destroy):
        threading.Thread.__init__(self)
        self.img = img

        self.logger = logger
        self.deviceLog = None
        self.generalLog = logging.getLogger("General")

        self.deviceName = deviceName
        self.soc = soc
        self.secure = False
        self.chipID = None
        self.part = part
        self.destroy = destroy

        self.daemon = True
        self.start()

    def GetDeviceDesciption(self):
        if self.chipID:
            return "[{0} ChipID:{1}]".format(self.deviceName, self.chipID)
        else:
            return "[{0}]".format(self.deviceName)

    def run(self):
        self.generalLog.info("{0}: Start burning".format(
            self.GetDeviceDesciption()))

        try:
            self.GetChipID()
            self.DestroyBoot()
            # self.UnloclUSBByPassword()
            self.CheckIfBoardIsSecure()
            self.InitializingDDR()
        except RuntimeError as exc:
            self.generalLog.error("{0}: While burning device exception occured:\n{1}".format(
                self.GetDeviceDesciption(), exc))

        self.generalLog.info("{0}: Finished burning".format(
            self.GetDeviceDesciption()))

    def InitializingDDR(self):
        if any(self.soc == item for item in ["gxl", "axg", "txlx"]):
            self.ExecUpdateCommandAssert(
                "cwr", [img.bl2, img.platformConfig.DDRLoad])
            self.ExecUpdateCommandAssert(
                "write", [img.ddr, img.platformConfig.bl2ParaAddr])
            self.ExecUpdateCommandAssert("run", [img.platformConfig.DDRRun])
            time.sleep(10)

            # retcode, out, err = self.ExecUpdateCommand("identify", ["7"])
            # еще какая-то хуева туча магии

        elif "g12a" == self.soc:

        elif "m8" == self.soc:

    def CheckIfBoardIsSecure(self):
        if "gxl" == self.soc:
            retcode, out, err = self.ExecUpdateCommand(
                "rreg", ["4", "0xc810022"])
            match = re.search(r'c810022:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x10) == 0x10
        elif any(self.soc == item for item in ["axg", "txlx", "g12a"]):
            retcode, out, err = self.ExecUpdateCommand(
                "rreg", ["4", "0xff800228"])
            match = re.search(r'ff800228:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x10) == 0x10
        elif "m8" == self.soc:
            retcode, out, err = self.ExecUpdateCommand(
                "rreg", ["4", "0xd9018048"])
            match = re.search(r'0xd9018048:\s*(\w+)', out.lower())
            if retcode == 0 and match:
                value = int(match.group(1), 16)
                self.secure = (value & 0x80) == 0x80

        if self.secure:
            self.generalLog.info("{0}: Board is in secure mode".format(
                self.GetDeviceDesciption()))
        else:
            self.generalLog.info("{0}: Board is not in secure mode".format(
                self.GetDeviceDesciption()))

    def GetChipID(self):
        """ Request ChipID of the device """
        try:
            self.generalLog.info("{0}: Read chipID".format(
                self.GetDeviceDesciption()))

            retcode, out, err = self.ExecUpdateCommandAssert("chipid")
            res = re.search(r'ChipID is:(\w+)', out)
            if res:
                self.chipID = res.group(1)
                self.deviceLog = logger.GetDeviceLog(
                    self.deviceName, self.chipID)
                self.generalLog.info("{0}: chipid = {1}".format(
                    self.GetDeviceDesciption(), self.chipID))
        except RuntimeError:
            self.generalLog.error(
                "{0}: Can't get chipid".format(self.GetDeviceDesciption()))
            raise

    def DestroyBoot(self):
        self.generalLog.info("{0}: Start destroy the boot".format(
            self.GetDeviceDesciption()))

        try:
            if any(self.part in part for part in ["all", "bootloader", "", "none"]):
                retcode, out, err = self.ExecUpdateCommand(
                    "bulkcmd", ["echo 12345"])
                if retcode == 0:
                    self.deviceLog.info("{0}: Rebooting the board".format(
                        self.GetDeviceDesciption()))
                    retcode, out, err = self.ExecUpdateCommand(
                        "bulkcmd", ["bootloader_is_old"])
                    retcode, out, err = self.ExecUpdateCommandAssert(
                        "bulkcmd", ["erase_bootloader"])
                    if self.destroy:
                        self.ExecUpdateCommand("bulkcmd", ["store erase boot"])
                        self.ExecUpdateCommand("bulkcmd", ["amlmmc erase 1"])
                        self.ExecUpdateCommand(
                            "bulkcmd", ["nand erase 0 4096"])

                    self.ExecUpdateCommand("bulkcmd", ["reset"])

                    if self.destroy:
                        self.generalLog("{0}: Destroy boot done".format(
                            self.GetDeviceDesciption()))
                        exit(0)

                    time.sleep(8)
            else:
                if self.destroy:
                    self.deviceLog("{0}: Seems board is already in usb mode, nothing to do more...".format(
                        self.GetDeviceDesciption()))

            if self.destroy:
                exit(0)

        except RuntimeError:
            self.deviceLog.error("{0}: Failed destroy the boot".format(
                self.GetDeviceDesciption()))
            raise

        self.generalLog.info("{0}: Finish destroy the boot".format(
            self.GetDeviceDesciption()))

    def ExecUpdateCommandAssert(self, cmd, args=[]):
        retcode, out, err = self.ExecUpdateCommand(cmd, args)
        if retcode != 0:
            raise RuntimeError("Returncode: {0} != 0".format(retcode))
        return retcode, out, err

    def ExecUpdateCommand(self, cmd, args=[]):
        """ Execute update tool for specific device

        Args:
            cmd (str): <command> of the update tool
            args (list, str): List of arguments for <command>. Defaults to [].

        Returns:
            int, str, str: Return result of execute command: retcode, stdout, stderr
        """
        # Kind of magic
        if any(cmd in i for i in ["bulkcmd", "tplcmd"]):
            args[0] = "     " + args[0]

        # Execute shell command
        execCmd = [cmd, "path-" + self.deviceName] + args
        retcode, out, err = exec_update(execCmd)

        # Choose logfile
        logfile = self.generalLog
        if self.deviceLog is not None:
            logfile = self.deviceLog

        # Logging command in logfile
        logfile.info("Command: {0}".format(' '.join(execCmd)))
        logfile.info(10 * "-" + " Response " + 10 * "-")
        if out != "":
            logfile.info(out)
        if err != "":
            logfile.error(err)
        logfile.info(30 * "-" + "\n")

        return retcode, out, err


def ParseArgs():
    def is_valid_file(parser, arg):
        if os.path.exists(arg):
            return arg
        else:
            parser.error("File {0} doesn't exist!".format(arg))

    parser = argparse.ArgumentParser(
        description="Argument parsing for automated flashing Amlogic devices",
        add_help=True)
    parser.add_argument("--img", dest="img", required=True, type=lambda x: is_valid_file(
        parser, x), help="Specify location path to aml_upgrade_package.img")
    parser.add_argument("--parts", dest="parts", required=True, choices=[
                        'all', 'none', 'bootloader', 'dtb', 'logo', 'recovery', 'boot', 'system'], help="Specify which partition to burn")
    parser.add_argument("--wipe", dest="wipe", default=False,
                        action='store_true', help="Destroy all partitions")
    parser.add_argument("--reset", dest="reset", default=False,
                        action='store_true', help="Force reset mode at the end of the burning")
    parser.add_argument("--soc", dest="soc", choices=["gxl", "axg", "txlx", "g12a", "m8"], default="gxl",
                        help="Force soc type (gxl=S905/S912,axg=A113,txlx=T962,g12a=S905X2,m8=S805/A111)")
    # parser.add_argument("--efuse-file", dest="efuse-file",  type=lambda x: is_valid_file(parser, x), help="Force efuse OTP burn, use this option carefully")
    # parser.add_argument("--uboot-file")
    # parser.add_argument("--password", dest="password", type=lambda x: is_valid_file(parser, x), help="Unlock usb mode using password file provided")
    parser.add_argument("--destroy", dest="destroy", default=False,
                        action='store_true', help="Erase the bootloader and reset the board")

    return parser.parse_args()


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

    def GetDeviceLog(self, deviceName, chipID):
        """Get logging instance to save all logs of burning process

        Args:
            deviceName (str): full device name, which used with update tools, like 'Bus 001 Device 009: ID 1b8e:c003' 
            chipID (str): chipid of this device 

        Returns:
            logging: instance of logging
        """

        logName = "{0}-{1}".format(deviceName,
                                   chipID).replace(' ', '_').replace(':', '_')
        fileHandlerDevice = logging.FileHandler(
            self.devicesLogDir + "/{0}.log".format(logName))
        fileHandlerDevice.setFormatter(self.formatter)

        logDevice = logging.getLogger(logName)
        logDevice.setLevel(logging.INFO)
        logDevice.addHandler(fileHandlerDevice)

        return logDevice


if __name__ == "__main__":
    logger = Logger()

    args = ParseArgs()

    generalLog = logging.getLogger("General")
    generalLog.info(exec_update(["identify", "7"]))

    img = Image(imgPath=args.img, soc=args.soc, parts=args.parts, secure=False)

    prev_devices = []
    regexp_device = re.compile(r'Bus \d+ Device \d+: ID \w+:\w+', re.MULTILINE)

    while(True):
        try:
            retcode, out, err = exec_update(["scan"])
            devices = []
            for match in regexp_device.finditer(out):
                deviceName = match.group(0)
                if deviceName not in prev_devices:
                    generalLog.info("New device: " + deviceName)
                    prev_devices.append(Burner(
                        img, logger, deviceName=deviceName, part="all", destroy=args.destroy, soc=args.soc))
                devices.append(deviceName)
            prev_devices = devices

        except RuntimeError as exc:
            generalLog.error("Device monitoring exception: {0}".format(exc))

        time.sleep(1)
